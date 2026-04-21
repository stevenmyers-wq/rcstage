import io
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

            # --- 2. LIVE STATE ---
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records', [])
            
            final_extensions = []
            seen_ids = set()
            
            # --- 3. OVERLAY SPREADSHEET ALIGNED TO SLOTS ---
            for i in range(1, 101):
                col_name = f"Line {i} Extension"
                val = row.get(col_name) if col_name in df.columns else None
                
                is_locked = False
                existing_ext = None
                
                # Check what RingCentral currently has on this exact physical slot
                if i <= len(live_records):
                    existing_record = live_records[i-1]
                    existing_ext = str(existing_record.get('extension', {}).get('id', ''))
                    is_locked = existing_record.get('notEditableOnHud', False)
                
                # Rule 1: If it is hardware-locked, keep it exactly as is
                if is_locked and existing_ext:
                    if existing_ext not in seen_ids:
                        final_extensions.append(existing_ext)
                        seen_ids.add(existing_ext)
                    continue

                # Rule 2: If the spreadsheet is blank, preserve the existing line
                if pd.isna(val) or str(val).strip() == "":
                    if existing_ext and existing_ext not in seen_ids:
                        final_extensions.append(existing_ext)
                        seen_ids.add(existing_ext)
                    continue
                    
                val_str = str(val).split('.')[0].strip()
                
                # Rule 3: Deletion
                if val_str.upper() == "CLEAR":
                    continue
                    
                # Rule 4: Addition / Update
                monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                if monitored_id not in seen_ids:
                    final_extensions.append(monitored_id)
                    seen_ids.add(monitored_id)

            # --- 4. BUILD STRICTLY SEQUENTIAL PAYLOAD ---
            payload_records = [{"id": str(idx + 1), "extension": {"id": ext}} for idx, ext in enumerate(final_extensions)]
            
            # Fast Check: Only send if the lists differ
            current_ids = [str(r.get('extension', {}).get('id')) for r in live_records if r.get('extension', {}).get('id')]
            payload_ids = [p['extension']['id'] for p in payload_records]
            
            has_changes = (current_ids != payload_ids)

            # --- 5. SEND ---
            if has_changes:
                try:
                    manager.update_monitored_lines(t_id, payload_records)
                    results["success"] += 1
                except Exception as e:
                    results["errors"].append(f"Ext {t_id}: {str(e)}")
            elif toggles:
                results["success"] += 1
            else:
                results["errors"].append(f"Ext {t_id}: No changes detected.")

        return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500
