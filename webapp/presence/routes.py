import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

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
        
        if not selected_users:
            return jsonify({"status": "error", "message": "No users selected"}), 400

        manager = RCPresenceManager()
        
        # 1. Fetch a master list of ALL extensions to do a recursive Name lookup
        all_exts = manager.get_all_extensions_raw()
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}

        audit_data = []

        for user in selected_users:
            ext_id = user.get('id')
            
            # Fetch settings (toggles) and lines (buttons)
            settings = manager.get_presence_settings(ext_id) or {}
            lines_response = manager.get_monitored_lines(ext_id)
            records = lines_response.get('records', [])

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id
            }

            # Add the overarching Presence Toggles on the left
            row["Ring on Monitored Call"] = settings.get('ringOnMonitoredCall', False)
            row["Enable Me to Pickup a Monitored Line"] = settings.get('pickUpCallsOnHold', False)
            row["Allow other users to see my presence status"] = settings.get('allowSeeMyPresence', False)

            # Add Line 1 to Line N (No skipping, no assumptions)
            for i, record in enumerate(records):
                line_num = i + 1
                ext_obj = record.get('extension', {})
                
                monitored_ext_id = str(ext_obj.get('id', ''))
                
                # Recursive Name Lookup: Try to find the actual name from our master list
                if monitored_ext_id and monitored_ext_id in id_to_ext_map:
                    master_ext = id_to_ext_map[monitored_ext_id]
                    name_val = master_ext.get('name') or ext_obj.get('type') or 'Unknown'
                    ext_val = master_ext.get('extensionNumber') or ext_obj.get('extensionNumber') or monitored_ext_id
                else:
                    # Fallback for speed dials, park locations, or detached numbers
                    name_val = ext_obj.get('name') or ext_obj.get('type') or record.get('type') or 'Unknown'
                    ext_val = ext_obj.get('extensionNumber') or monitored_ext_id or record.get('phoneNumber') or record.get('number') or ''
                    
                    # If it's a completely undocumented button type, dump the keys so we know it exists
                    if not ext_val and name_val == 'Unknown':
                        name_val = f"Custom Button: {list(record.keys())}"

                row[f"Line {line_num} Name"] = str(name_val)
                row[f"Line {line_num} Extension"] = str(ext_val)
                
            audit_data.append(row)
        
        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Presence_Audit')
        
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='BLF_Presence_Audit.xlsx'
        )

    except Exception as e:
        logging.error(f"Audit Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def download_template():
    try:
        df = pd.DataFrame({
            "Target Extension Name": ["Steve Mobile", "Test Account"],
            "Target Extension Number": ["11134", "11135"],
            "Target Extension ID": ["281658124", "281658125"],
            "Ring on Monitored Call": [False, True],
            "Enable Me to Pickup a Monitored Line": [True, False],
            "Allow other users to see my presence status": [True, True],
            "Line 1 Name": ["Steve Mobile", "Main Queue"],
            "Line 1 Extension": ["11134", "11135"],
            "Line 2 Name": ["Steven Smyers", "Speed Dial Mom"],
            "Line 2 Extension": ["11116", "987654321"],
            "Line 3 Name": ["Some Shared Line", ""],
            "Line 3 Extension": ["81827", ""]
        })
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='BLF_Template')

        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Update_Template.xlsx')
    except Exception as e:
         return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf_from_file():
    try:
        if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
        
        file = request.files['file']
        df = pd.read_excel(file)
        
        if "Target Extension ID" not in df.columns:
            return jsonify({"status": "error", "message": "Missing column: Target Extension ID"}), 400

        manager = RCPresenceManager()
        results = {"success": 0, "errors": []}

        # Build the Translator (Extension Number -> ID)
        all_exts = manager.get_all_extensions_raw()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        id_set = {str(e.get('id')) for e in all_exts}

        # Get all dynamic "Line X Extension" columns (we ignore the Name columns on upload)
        line_ext_cols = [c for c in df.columns if str(c).startswith("Line ") and str(c).endswith("Extension")]
        line_ext_cols.sort(key=lambda x: int(x.split(' ')[1]) if len(x.split(' ')) > 1 and x.split(' ')[1].isdigit() else 999)

        for index, row in df.iterrows():
            target_id = str(row["Target Extension ID"]).split('.')[0].strip()
            if not target_id or target_id.lower() == 'nan': continue
            
            # --- UPDATE SETTINGS TOGGLES ---
            settings_payload = {}
            if "Ring on Monitored Call" in row and pd.notna(row["Ring on Monitored Call"]):
                settings_payload["ringOnMonitoredCall"] = bool(row["Ring on Monitored Call"])
            if "Enable Me to Pickup a Monitored Line" in row and pd.notna(row["Enable Me to Pickup a Monitored Line"]):
                settings_payload["pickUpCallsOnHold"] = bool(row["Enable Me to Pickup a Monitored Line"])
            if "Allow other users to see my presence status" in row and pd.notna(row["Allow other users to see my presence status"]):
                settings_payload["allowSeeMyPresence"] = bool(row["Allow other users to see my presence status"])
                
            if settings_payload:
                try:
                    manager.update_presence_settings(target_id, settings_payload)
                except Exception as e:
                    results["errors"].append(f"Ext {target_id}: Settings update failed - {str(e)}")

            # --- UPDATE BLF LINES ---
            new_records = []

            # We process ALL lines strictly as they are written in the spreadsheet. 
            # No assumptions are made about lines 1 and 2.
            for col in line_ext_cols:
                val = row[col]
                if pd.notna(val) and str(val).strip() != "":
                    raw_val = str(val).split('.')[0].strip()
                    
                    # Translator Engine: Is it an ID? Is it a Number?
                    if raw_val in id_set:
                        monitored_id = raw_val
                    elif raw_val in ext_map:
                        monitored_id = ext_map[raw_val]
                    else:
                        monitored_id = raw_val # Fallback (e.g., Speed Dial ID or arbitrary string)
                        
                    new_records.append({"extension": {"id": monitored_id}})

            try:
                manager.update_monitored_lines(target_id, new_records)
                results["success"] += 1
            except Exception as e:
                # If RingCentral DOES reject altering line 1/2 for a specific user type, it will be caught here.
                results["errors"].append(f"Ext {target_id}: BLF lines update failed - {str(e)}")

        return jsonify({
            "status": "completed", 
            "message": f"Processed BLF updates for {results['success']} users.",
            "errors": results["errors"]
        })

    except Exception as e:
        logging.error(f"Upload Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
