import io
import re
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

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
        
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
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

            assigned_map = {str(r.get('id')): r for r in records}
            
            for i in range(1, 101):
                record = assigned_map.get(str(i))
                if record:
                    ext_obj = record.get('extension') or {}
                    m_id = str(ext_obj.get('id', ''))
                    
                    master = id_to_ext_map.get(m_id, {})
                    type_label = master.get('type') or ext_obj.get('type') or 'Unknown'
                    name = master.get('name') or ext_obj.get('name') or type_label
                    ext_num = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
                    
                    lock_status = "[LOCKED] " if record.get('notEditableOnHud') else ""
                    row[f"Line {i} Name"] = f"{lock_status}{name} ({type_label})"
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
        logging.exception("Audit Crash")
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

        for _, row in df.iterrows():
            target_col = next((c for c in df.columns if "target extension id" in c.lower()), None)
            if not target_col: continue
            
            t_id = str(row.get(target_col, "")).split('.')[0].strip()
            if not t_id or t_id.lower() == 'nan': continue
            
            # --- 1. TOGGLES ---
            toggles = {}
            for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"), 
                               ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold"),
                               ("Allow other users to see my presence status", "allowSeeMyPresence")]:
                val = parse_bool(row.get(key))
                if val is not None: toggles[field] = val
            if toggles: manager.update_presence_settings(t_id, toggles)

            # --- 2. FETCH LIVE STATE ---
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records', [])
            
            current_state = {}
            locked_slots = set()
            
            for r in live_records:
                l_id = str(r.get('id'))
                ext_id = r.get('extension', {}).get('id')
                if ext_id: current_state[l_id] = str(ext_id)
                if r.get('notEditableOnHud'): locked_slots.add(l_id)

            # --- 3. OVERLAY SPREADSHEET ---
            ordered_extensions = []
            seen_monitored = set()
            
            for i in range(1, 101):
                l_idx = str(i)
                sheet_col = f"Line {i} Extension"
                
                # If it's locked, keep it exactly as is
                if l_idx in locked_slots:
                    ordered_extensions.append(current_state[l_idx])
                    seen_monitored.add(current_state[l_idx])
                    continue
                    
                val = row.get(sheet_col) if sheet_col in df.columns else None
                
                # If Blank, keep what's there
                if pd.isna(val) or str(val).strip() == "":
                    if l_idx in current_state:
                        # Prevent duplicate copying
                        if current_state[l_idx] not in seen_monitored:
                            ordered_extensions.append(current_state[l_idx])
                            seen_monitored.add(current_state[l_idx])
                    continue 
                    
                val_str = str(val).split('.')[0].strip()
                
                # "CLEAR" deletes it
                if val_str.upper() == "CLEAR":
                    continue 
                    
                # Find ID and Add
                monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                
                if monitored_id in seen_monitored:
                    results["errors"].append(f"Ext {t_id}: Skipped duplicate extension {val_str} on Line {i}.")
                    continue
                    
                ordered_extensions.append(monitored_id)
                seen_monitored.add(monitored_id)

            # --- 4. BUILD PAYLOAD ---
            payload_records = []
            for index, ext_id in enumerate(ordered_extensions):
                payload_records.append({
                    "id": str(index + 1),
                    "extension": {"id": ext_id}
                })
            
            has_changes = (len(payload_records) != len(current_state)) or any(current_state.get(p["id"]) != p["extension"]["id"] for p in payload_records)

            # --- 5. SEND TO RINGCENTRAL ---
            if has_changes:
                try:
                    manager.update_monitored_lines(t_id, payload_records)
                    results["success"] += 1
                except Exception as e:
                    results["errors"].append(str(e))
            elif toggles:
                results["success"] += 1
            else:
                results["errors"].append(f"Ext {t_id}: No changes detected.")

        return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500
