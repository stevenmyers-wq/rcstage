import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/sites', methods=['GET'])
def get_sites():
    try:
        manager = RCPresenceManager()
        sites = manager.get_sites()
        return jsonify({"status": "success", "sites": sites})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def get_template():
    try:
        columns = ["Target Extension Name", "Target Extension Number", "Target Extension ID", 
                   "Ring on Monitored Call", "Enable Me to Pickup a Monitored Line", 
                   "Allow other users to see my presence status"]
        for i in range(1, 101):
            columns.append(f"Line {i} Name")
            columns.append(f"Line {i} Extension")
        df_template = pd.DataFrame(columns=columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_template.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='Template.xlsx')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
            
            for i, record in enumerate(records):
                line_idx = i + 1
                ext_obj = record.get('extension') or {}
                m_id = str(ext_obj.get('id', ''))
                
                master = id_to_ext_map.get(m_id, {})
                type_label = master.get('type') or ext_obj.get('type') or 'Unknown'
                name = master.get('name') or ext_obj.get('name') or type_label
                ext_num = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
                
                lock_status = "[LOCKED] " if record.get('notEditableOnHud') else ""
                row[f"Line {line_idx} Name"] = f"{lock_status}{name} ({type_label})"
                row[f"Line {line_idx} Extension"] = str(ext_num)
            
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

            # --- 2. GET CURRENT STATE (The Real IDs) ---
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records', [])
            
            payload_records = []
            seen_extensions = set()

            # --- 3. MAP SPREADSHEET TO EXISTING REAL IDs ---
            for i, record in enumerate(live_records):
                # Extract the literal system identifier, whatever format it is
                real_slot_id = str(record.get('id')) 
                is_locked = record.get('notEditableOnHud', False)
                current_ext_id = str(record.get('extension', {}).get('id', ''))
                
                # Look at the corresponding column in the spreadsheet
                sheet_col = f"Line {i + 1} Extension"
                val = row.get(sheet_col) if sheet_col in df.columns else None
                
                # Rule 1: Hardware locked primary lines
                if is_locked:
                    if current_ext_id:
                        payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                        seen_extensions.add(current_ext_id)
                    continue
                
                # Rule 2: Blank spreadsheet cell -> keep existing configuration
                if pd.isna(val) or str(val).strip() == "":
                    if current_ext_id and current_ext_id not in seen_extensions:
                        payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                        seen_extensions.add(current_ext_id)
                    continue
                
                val_str = str(val).split('.')[0].strip()
                
                # Rule 3: Clear intent
                if val_str.upper() == "CLEAR":
                    continue # Omitting the ID from the payload clears the slot
                
                # Rule 4: Update with new extension
                monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                if monitored_id in seen_extensions:
                    continue # Stop duplicates
                
                # Build the minimal requested object using the REAL id
                payload_records.append({
                    "id": real_slot_id,
                    "extension": {"id": monitored_id}
                })
                seen_extensions.add(monitored_id)

            # Check if user tried to add lines beyond what the system has IDs for
            skipped_lines = []
            for i in range(len(live_records) + 1, 101):
                sheet_col = f"Line {i} Extension"
                val = row.get(sheet_col) if sheet_col in df.columns else None
                if not pd.isna(val) and str(val).strip() != "" and str(val).strip().upper() != "CLEAR":
                    skipped_lines.append(str(i))
                    
            if skipped_lines:
                results["errors"].append(f"Ext {t_id}: Lines {', '.join(skipped_lines)} were ignored because the system has no available internal IDs for those slots.")

            # --- 4. DIFF AND SEND ---
            current_state = {str(r.get('id')): str(r.get('extension', {}).get('id')) for r in live_records}
            payload_state = {p['id']: p['extension']['id'] for p in payload_records}
            
            if current_state != payload_state:
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
