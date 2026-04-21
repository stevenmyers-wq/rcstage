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
        
        # Regex finds columns safely regardless of spaces
        line_cols = [c for c in df.columns if "line" in c.lower() and "extension" in c.lower()]

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

            # --- 2. LIVE STATE ---
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records', [])
            
            final_lines = {}
            locked_slots = set()
            
            for r in live_records:
                l_id = str(r.get('id'))
                ext_id = r.get('extension', {}).get('id')
                if ext_id:
                    final_lines[l_id] = str(ext_id)
                if r.get('notEditableOnHud'):
                    locked_slots.add(l_id)

            has_changes = False

            # --- 3. OVERLAY SPREADSHEET ---
            for col in line_cols:
                # Safely extract line number using Regex
                match = re.search(r'\d+', col)
                if not match: continue
                l_idx = str(match.group())
                
                val = row.get(col)
                if pd.isna(val) or str(val).strip() == "": continue 
                
                val_str = str(val).split('.')[0].strip()
                
                # CLEAR INTENT
                if val_str.upper() == "CLEAR":
                    if l_idx in locked_slots:
                        results["errors"].append(f"Ext {t_id}: Cannot clear Line {l_idx} (Locked by RC).")
                    elif l_idx in final_lines:
                        del final_lines[l_idx]
                        has_changes = True
                    continue
                
                # UPDATE INTENT
                if l_idx in locked_slots:
                    results["errors"].append(f"Ext {t_id}: Cannot update Line {l_idx} (Locked by RC).")
                    continue
                    
                monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                
                if final_lines.get(l_idx) != monitored_id:
                    final_lines[l_idx] = monitored_id
                    has_changes = True

            # --- 4. BUILD PAYLOAD & STRIP LOCKED LINES ---
            if has_changes:
                payload_records = []
                for k, v in final_lines.items():
                    # THE FIX: Completely exclude hardware-locked lines from the request
                    if k in locked_slots:
                        continue
                    payload_records.append({"id": k, "extension": {"id": v}})
                
                try:
                    manager.update_monitored_lines(t_id, payload_records)
                    results["success"] += 1
                except Exception as e:
                    error_msg = str(e)
                    if "Presence-102" in error_msg:
                        results["errors"].append(f"Ext {t_id}: HUD Limitation. The user's hardware doesn't support this configuration.")
                    else:
                        results["errors"].append(f"Ext {t_id}: {error_msg}")
            elif toggles:
                results["success"] += 1
            else:
                results["errors"].append(f"Ext {t_id}: No changes detected in the spreadsheet.")

        return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/diagnose/<target_ext_id>', methods=['GET'])
def diagnose_blf(target_ext_id):
    """Diagnostic tool to run the Echo Test and expose raw API responses."""
    from webapp.rc_api import rc_api_call
    manager = RCPresenceManager()
    
    diagnostic_log = {
        "1_Target": target_ext_id,
        "2_GET_Request": "Attempting to fetch current state...",
        "3_Current_State": None,
        "4_PUT_Request": "Attempting to echo exact state back...",
        "5_PUT_Response": None,
        "6_Error_Details": None
    }

    try:
        # 1. Fetch current state
        current_data = rc_api_call(f"{manager.base_path}/extension/{target_ext_id}/presence/line", method="GET")
        current_records = current_data.get('records', [])
        diagnostic_log["3_Current_State"] = current_records

        # 2. Build the Echo Payload (Exactly as the schema requests)
        # We only pass 'id' and 'extension: {id}'
        echo_records = []
        for r in current_records:
            ext_id = r.get('extension', {}).get('id')
            if ext_id:
                echo_records.append({
                    "id": str(r.get('id')),
                    "extension": {"id": str(ext_id)}
                })
        
        diagnostic_log["4_PUT_Request"] = echo_records

        # 3. Fire the PUT request
        put_response = rc_api_call(f"{manager.base_path}/extension/{target_ext_id}/presence/line", method="PUT", json={"records": echo_records})
        diagnostic_log["5_PUT_Response"] = put_response

        return jsonify({"status": "SUCCESS - Echo Test Passed", "diagnostics": diagnostic_log})

    except Exception as e:
        # Catch the exact HTTP response body from RingCentral
        diagnostic_log["5_PUT_Response"] = "FAILED"
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            diagnostic_log["6_Error_Details"] = e.response.text
        else:
            diagnostic_log["6_Error_Details"] = str(e)
            
        return jsonify({"status": "FAILED - 400 Bad Request", "diagnostics": diagnostic_log}), 400
