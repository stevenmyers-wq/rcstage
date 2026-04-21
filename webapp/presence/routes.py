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

@presence_bp.route('/api/presence/users', methods=['GET'])
def get_users():
    try:
        site_id = request.args.get('site_id')
        manager = RCPresenceManager()
        users = manager.get_all_users(site_id=site_id)
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def get_template():
    try:
        columns = [
            "Target Extension Name",
            "Target Extension Number",
            "Target Extension ID",
            "Ring on Monitored Call",
            "Enable Me to Pickup a Monitored Line",
            "Allow other users to see my presence status"
        ]
        
        # Add BOTH Name and Extension columns to match the Audit report perfectly
        for i in range(1, 101):
            columns.append(f"Line {i} Name")
            columns.append(f"Line {i} Extension")
            
        df_template = pd.DataFrame(columns=columns)
        
        # --- Build Example Row ---
        example_row = {col: "" for col in columns}
        example_row["Target Extension Name"] = "John Doe (Informational)"
        example_row["Target Extension Number"] = "101 (Informational)"
        example_row["Target Extension ID"] = "123456789 (REQUIRED)"
        example_row["Ring on Monitored Call"] = "TRUE"
        example_row["Enable Me to Pickup a Monitored Line"] = "FALSE"
        example_row["Allow other users to see my presence status"] = "TRUE"
        
        example_row["Line 1 Name"] = "Jane Smith (Informational)"
        example_row["Line 1 Extension"] = "Leave blank if keeping existing/locked"
        
        example_row["Line 2 Name"] = "New Hire (Informational)"
        example_row["Line 2 Extension"] = "233306125 (Will assign this ext to Line 2)"
        
        example_row["Line 3 Name"] = "CLEAR"
        example_row["Line 3 Extension"] = "CLEAR (Will wipe the existing user on Line 3)"
        
        df_examples = pd.DataFrame([example_row])
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_template.to_excel(writer, sheet_name='Template', index=False)
            df_examples.to_excel(writer, sheet_name='Examples', index=False)
            
            # Auto-widen columns on the Examples tab so it is readable
            worksheet = writer.sheets['Examples']
            for col in worksheet.columns:
                max_length = 0
                column_letter = col[0].column_letter 
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)
                
        output.seek(0)
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
            as_attachment=True, 
            download_name='BLF_Update_Template.xlsx'
        )
    except Exception as e:
        logging.exception("Template Generation Crash")
        return jsonify({"status": "error", "message": f"Failed to generate template: {str(e)}"}), 500

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
                real_slot_id = str(record.get('id')) 
                is_locked = record.get('notEditableOnHud', False)
                current_ext_id = str(record.get('extension', {}).get('id', ''))
                
                sheet_col = f"Line {i + 1} Extension"
                val = row.get(sheet_col) if sheet_col in df.columns else None
                
                if is_locked:
                    if current_ext_id:
                        payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                        seen_extensions.add(current_ext_id)
                    continue
                
                if pd.isna(val) or str(val).strip() == "":
                    if current_ext_id and current_ext_id not in seen_extensions:
                        payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                        seen_extensions.add(current_ext_id)
                    continue
                
                val_str = str(val).split('.')[0].strip()
                
                if val_str.upper() == "CLEAR":
                    continue 
                
                monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                if monitored_id in seen_extensions:
                    continue 
                
                payload_records.append({
                    "id": real_slot_id,
                    "extension": {"id": monitored_id}
                })
                seen_extensions.add(monitored_id)

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
