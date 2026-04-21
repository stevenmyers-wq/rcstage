import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/users', methods=['GET'])
def get_users():
    try:
        manager = RCPresenceManager()
        users = manager.get_all_users()
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/audit', methods=['POST'])
def generate_audit_report():
    try:
        data = request.json
        selected_users = data.get('users', [])
        manager = RCPresenceManager()
        
        # Build a master map to resolve names and 'SharedLinesGroup' types
        all_exts = manager.get_all_extensions_raw()
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}
        
        audit_data = []
        for user in selected_users:
            ext_id = user.get('id')
            settings = manager.get_presence_settings(ext_id)
            lines_resp = manager.get_monitored_lines(ext_id)
            records = lines_resp.get('records') or []

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id,
                "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
            }

            # Map existing lines 1-100
            for record in records:
                l_id = record.get('id')
                ext_info = record.get('extension') or {}
                m_id = str(ext_info.get('id', ''))
                
                # Cross-reference with master list to find true type (SharedLine, etc.)
                master = id_to_ext_map.get(m_id, {})
                name = master.get('name') or ext_info.get('name') or 'Unknown'
                ext_num = master.get('extensionNumber') or ext_info.get('extensionNumber') or m_id
                type_label = master.get('type') or ext_info.get('type') or "Unknown"
                
                lock_status = "[LOCKED] " if record.get('notEditableOnHud') else ""
                row[f"Line {l_id} Name"] = f"{lock_status}{name} ({type_label})"
                row[f"Line {l_id} Extension"] = str(ext_num)

        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Audit.xlsx')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf():
    try:
        file = request.files['file']
        df = pd.read_excel(file, sheet_name=0)
        df.columns = df.columns.str.strip()
        
        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw()
        ext_num_to_id = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        
        results = {"success": 0, "errors": []}
        line_cols = [c for c in df.columns if "Line" in c and "Extension" in c]

        for _, row in df.iterrows():
            t_id = str(row["Target Extension ID"]).split('.')[0]
            
            # Step 1: Handle Toggles
            toggles = {}
            for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"), 
                               ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold"),
                               ("Allow other users to see my presence status", "allowSeeMyPresence")]:
                val = parse_bool(row.get(key))
                if val is not None: toggles[field] = val
            if toggles: manager.update_presence_settings(t_id, toggles)

            # Step 2: Handle Monitored Lines (The 2-Step Process)
            # Fetch current state to find hardware-locked lines
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records') or []
            
            # Build payload, DISREGARDING any spreadsheet changes for locked lines [cite: 31]
            update_payload = []
            locked_indices = {str(r['id']) for r in live_records if r.get('notEditableOnHud')}

            for col in line_cols:
                l_idx = col.split(' ')[1]
                
                # If this slot is locked (Line 1/2 or other primary), ignore spreadsheet value
                if l_idx in locked_indices:
                    continue
                
                val = str(row[col]).split('.')[0].strip() if pd.notna(row[col]) else ""
                if not val: continue # Skip empty cells
                
                # Resolve internal ID
                monitored_id = ext_num_to_id.get(val) or val
                update_payload.append({"id": l_idx, "extension": {"id": monitored_id}})

            try:
                if update_payload:
                    manager.update_monitored_lines(t_id, update_payload)
                results["success"] += 1
            except Exception as e:
                results["errors"].append(f"Ext {t_id}: {str(e)}")

        return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
