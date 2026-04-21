import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/audit', methods=['POST'])
def generate_audit_report():
    try:
        data = request.json
        selected_users = data.get('users', [])
        manager = RCPresenceManager()
        
        # Pull master extension list to identify types like SharedLinesGroup [cite: 29]
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}
        
        audit_data = []
        for user in selected_users:
            ext_id = user.get('id')
            settings = manager.get_presence_settings(ext_id)
            lines_resp = manager.get_monitored_lines(ext_id) # 
            records = lines_resp.get('records') or [] # [cite: 24, 25]

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id,
                "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
            }

            # Map existing lines 1-100 based on API response [cite: 30]
            assigned_map = {str(r.get('id')): r for r in records}
            
            for i in range(1, 101):
                record = assigned_map.get(str(i))
                if record:
                    ext_obj = record.get('extension') or {} # [cite: 32]
                    m_id = str(ext_obj.get('id', ''))
                    master = id_to_ext_map.get(m_id, {})
                    
                    # Identify the true type (User, SharedLinesGroup, etc.) [cite: 23, 29]
                    type_label = master.get('type') or ext_obj.get('type') or 'Unknown'
                    name = master.get('name') or ext_obj.get('name') or type_label
                    ext_num = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
                    
                    # Add 'Locked' prefix if notEditableOnHud is true 
                    locked_prefix = "[LOCKED] " if record.get('notEditableOnHud') else ""
                    
                    row[f"Line {i} Name"] = f"{locked_prefix}{name} ({type_label})"
                    row[f"Line {i} Extension"] = str(ext_num)
                else:
                    row[f"Line {i} Name"] = ""
                    row[f"Line {i} Extension"] = ""
            
            audit_data.append(row)
            
        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Audit_Detailed.xlsx')
    except Exception as e:
        return jsonify({"status": "error", "message": f"Audit Failed: {str(e)}"}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf():
    try:
        file = request.files['file']
        df = pd.read_excel(file, sheet_name=0)
        df.columns = df.columns.str.strip()
        
        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        
        results = {"success": 0, "errors": []}
        line_cols = [c for c in df.columns if "Line" in c and "Extension" in c]

        for _, row in df.iterrows():
            t_id = str(row["Target Extension ID"]).split('.')[0]
            
            # Update Toggle Settings
            toggles = {}
            for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"), 
                               ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold"),
                               ("Allow other users to see my presence status", "allowSeeMyPresence")]:
                val = parse_bool(row.get(key))
                if val is not None: toggles[field] = val
            if toggles: manager.update_presence_settings(t_id, toggles)

            # --- TWO-STEP MERGE START ---
            # Fetch live records to detect hardware-locked lines (notEditableOnHud) 
            live_lines = manager.get_monitored_lines(t_id).get('records', [])
            
            # Build current state dictionary: { line_id: { ext_id, is_locked } } [cite: 30, 31, 32]
            live_state = {}
            for r in live_lines:
                l_id = str(r['id'])
                e_id = r.get('extension', {}).get('id')
                if e_id:
                    live_state[l_id] = {
                        "id": str(e_id),
                        "locked": r.get('notEditableOnHud', False) # Critical check 
                    }

            # Process spreadsheet data slot-by-slot
            for col in line_cols:
                line_idx = col.split(' ')[1]
                
                # If the API says this physical slot is locked, ignore the spreadsheet [cite: 26, 31, 39]
                if live_state.get(line_idx, {}).get('locked'):
                    continue
                
                new_val = str(row[col]).split('.')[0].strip() if pd.notna(row[col]) else ""
                
                if not new_val:
                    # Remove line if editable and spreadsheet is blank
                    if line_idx in live_state:
                        del live_state[line_idx]
                else:
                    # Translate extension number to internal ID
                    monitored_id = ext_map.get(new_val) or manager.get_extension_by_number(new_val) or new_val
                    live_state[line_idx] = {"id": str(monitored_id), "locked": False}

            # Reconstruct the sequential payload for PUT [cite: 7, 37]
            # Use 'id' and 'extension: {id}' as required by schema [cite: 35]
            final_records = [
                {"id": k, "extension": {"id": v["id"]}} 
                for k, v in sorted(live_state.items(), key=lambda x: int(x[0]))
            ]

            try:
                if final_records:
                    manager.update_monitored_lines(t_id, final_records)
                results["success"] += 1
            except Exception as e:
                results["errors"].append(f"Ext {t_id}: {str(e)}")

        return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
