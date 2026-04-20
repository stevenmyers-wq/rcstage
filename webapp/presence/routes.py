import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    """Safely converts Excel values to strict JSON booleans."""
    if pd.isna(val) or val == "": 
        return None
    if isinstance(val, bool): 
        return val
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
        
        if not selected_users:
            return jsonify({"status": "error", "message": "No users selected"}), 400

        manager = RCPresenceManager()
        
        # Fetch master list for recursive Name lookup
        all_exts = manager.get_all_extensions_raw()
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}

        audit_data = []

        for user in selected_users:
            ext_id = user.get('id')
            
            settings = manager.get_presence_settings(ext_id) or {}
            lines_response = manager.get_monitored_lines(ext_id)
            records = lines_response.get('records', [])

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id,
                "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
            }

            # Map the actual assigned lines
            assigned_lines = {}
            for record in records:
                # The API returns the line ID as a string number (e.g., "1", "2")
                line_id_str = record.get('id')
                if not line_id_str or not str(line_id_str).isdigit(): continue
                
                line_num = int(line_id_str)
                ext_obj = record.get('extension', {})
                monitored_ext_id = str(ext_obj.get('id', ''))
                
                # Recursive Name Lookup
                if monitored_ext_id and monitored_ext_id in id_to_ext_map:
                    master_ext = id_to_ext_map[monitored_ext_id]
                    name_val = master_ext.get('name') or ext_obj.get('type') or 'Unknown'
                    ext_val = master_ext.get('extensionNumber') or ext_obj.get('extensionNumber') or monitored_ext_id
                else:
                    name_val = ext_obj.get('name') or ext_obj.get('type') or record.get('type') or 'Unknown'
                    ext_val = ext_obj.get('extensionNumber') or monitored_ext_id or record.get('phoneNumber') or record.get('number') or ''
                    if not ext_val and name_val == 'Unknown':
                        name_val = f"Custom Button: {list(record.keys())}"

                assigned_lines[line_num] = (name_val, ext_val)

            # Pad out to 100 columns to match the template exactly
            for i in range(1, 101):
                if i in assigned_lines:
                    row[f"Line {i} Name"] = str(assigned_lines[i][0])
                    row[f"Line {i} Extension"] = str(assigned_lines[i][1])
                else:
                    row[f"Line {i} Name"] = ""
                    row[f"Line {i} Extension"] = ""
                
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
        # 1. Generate Base Columns
        cols = [
            "Target Extension Name", "Target Extension Number", "Target Extension ID",
            "Ring on Monitored Call", "Enable Me to Pickup a Monitored Line", "Allow other users to see my presence status"
        ]
        
        # Extend to 100 lines
        for i in range(1, 101):
            cols.extend([f"Line {i} Name", f"Line {i} Extension"])
            
        # Create Blank DataFrame for the active Input sheet
        df_blank = pd.DataFrame(columns=cols)

        # 2. Generate the Example DataFrame
        example_data = {
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
        }
        
        # Pad the example out to 100 columns with blanks so it shares the schema
        for i in range(4, 101):
            example_data[f"Line {i} Name"] = ["", ""]
            example_data[f"Line {i} Extension"] = ["", ""]
            
        df_example = pd.DataFrame(example_data)
        
        # 3. Write Dual-Tab Excel File
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_blank.to_excel(writer, index=False, sheet_name='BLF_Update')
            df_example.to_excel(writer, index=False, sheet_name='Examples')

        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Update_Template.xlsx')
    except Exception as e:
         return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf_from_file():
    try:
        if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
        
        file = request.files['file']
        
        # Explicitly read the FIRST sheet (sheet_name=0) so the Example tab is ignored
        df = pd.read_excel(file, sheet_name=0)
        
        if "Target Extension ID" not in df.columns:
            return jsonify({"status": "error", "message": "Missing column: Target Extension ID on the first sheet."}), 400

        manager = RCPresenceManager()
        results = {"success": 0, "errors": []}

        # Build the Translator (Extension Number -> ID)
        all_exts = manager.get_all_extensions_raw()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        id_set = {str(e.get('id')) for e in all_exts}

        # Identify all Line Extension columns dynamically
        line_ext_cols = [c for c in df.columns if str(c).startswith("Line ") and str(c).endswith("Extension")]
        line_ext_cols.sort(key=lambda x: int(x.split(' ')[1]) if len(x.split(' ')) > 1 and x.split(' ')[1].isdigit() else 999)

        for index, row in df.iterrows():
            target_id = str(row.get("Target Extension ID", "")).split('.')[0].strip()
            if not target_id or target_id.lower() == 'nan': continue
            
            # --- 1. UPDATE SETTINGS TOGGLES (Safely parsed as Booleans) ---
            settings_payload = {}
            ring_val = parse_bool(row.get("Ring on Monitored Call"))
            pickup_val = parse_bool(row.get("Enable Me to Pickup a Monitored Line"))
            see_val = parse_bool(row.get("Allow other users to see my presence status"))

            if ring_val is not None: settings_payload["ringOnMonitoredCall"] = ring_val
            if pickup_val is not None: settings_payload["pickUpCallsOnHold"] = pickup_val
            if see_val is not None: settings_payload["allowSeeMyPresence"] = see_val
                
            if settings_payload:
                try:
                    manager.update_presence_settings(target_id, settings_payload)
                except Exception as e:
                    results["errors"].append(f"Ext {target_id}: Settings update failed - {str(e)}")

            # --- 2. UPDATE BLF LINES (Explicitly mapping the 'id' sequence) ---
            new_records = []

            for col in line_ext_cols:
                val = row.get(col)
                if pd.notna(val) and str(val).strip() != "":
                    # Extract the Line sequence number (e.g. "Line 1 Extension" -> 1)
                    line_num = int(col.split(' ')[1])
                    raw_val = str(val).split('.')[0].strip()
                    
                    # Translator Engine
                    if raw_val in id_set:
                        monitored_id = raw_val
                    elif raw_val in ext_map:
                        monitored_id = ext_map[raw_val]
                    else:
                        monitored_id = raw_val # Fallback (Speed Dials)
                        
                    # API requires the "id" parameter to bind to the specific line slot
                    new_records.append({
                        "id": str(line_num), 
                        "extension": {"id": monitored_id}
                    })

            if new_records:
                try:
                    manager.update_monitored_lines(target_id, new_records)
                    results["success"] += 1
                except Exception as e:
                    results["errors"].append(f"Ext {target_id}: BLF update failed - {str(e)}")

        return jsonify({
            "status": "completed", 
            "message": f"Processed updates for {results['success']} users.",
            "errors": results["errors"]
        })

    except Exception as e:
        logging.error(f"Upload Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
