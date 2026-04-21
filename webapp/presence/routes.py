import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    """Safely converts Excel/CSV values to strict JSON booleans."""
    if pd.isna(val) or str(val).strip() == "": 
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
        
        all_exts = manager.get_all_extensions_raw() or []
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}

        audit_data = []

        for user in selected_users:
            ext_id = user.get('id')
            
            # Use 'or {}' and 'or []' to prevent NoneType crashes from null API payloads
            settings = manager.get_presence_settings(ext_id) or {}
            lines_response = manager.get_monitored_lines(ext_id) or {}
            records = lines_response.get('records') or []

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id,
                "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
            }

            assigned_lines = {}
            for record in records:
                line_id_str = record.get('id')
                if not line_id_str or not str(line_id_str).isdigit(): continue
                
                line_num = int(line_id_str)
                # Safely extract the extension object even if the API passes null
                ext_obj = record.get('extension') or {}
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
        cols = [
            "Target Extension Name", "Target Extension Number", "Target Extension ID",
            "Ring on Monitored Call", "Enable Me to Pickup a Monitored Line", "Allow other users to see my presence status"
        ]
        for i in range(1, 101):
            cols.extend([f"Line {i} Name", f"Line {i} Extension"])
            
        df_blank = pd.DataFrame(columns=cols)

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
        for i in range(4, 101):
            example_data[f"Line {i} Name"] = ["", ""]
            example_data[f"Line {i} Extension"] = ["", ""]
            
        df_example = pd.DataFrame(example_data)
        
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
        if 'file' not in request.files: 
            return jsonify({"status": "error", "message": "No file uploaded"}), 400
        
        file = request.files['file']
        filename = file.filename.lower()
        
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file, sheet_name=0)
            
        df.columns = df.columns.astype(str).str.replace('\ufeff', '').str.strip()

        target_col = next((c for c in df.columns if "target extension id" in c.lower()), None)
        if not target_col:
            return jsonify({"status": "error", "message": "Missing column: Target Extension ID."}), 400

        manager = RCPresenceManager()
        results = {"success": 0, "errors": []}

        all_exts = manager.get_all_extensions_raw() or []
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        id_set = {str(e.get('id')) for e in all_exts}

        ring_col = next((c for c in df.columns if "ring on monitored" in c.lower()), None)
        pickup_col = next((c for c in df.columns if "pickup a monitored" in c.lower()), None)
        see_col = next((c for c in df.columns if "see my presence" in c.lower()), None)
        line_ext_cols = [c for c in df.columns if c.lower().startswith("line ") and c.lower().endswith("extension")]

        records = df.to_dict('records')

        for row in records:
            target_id = str(row.get(target_col, "")).split('.')[0].strip()
            if not target_id or target_id.lower() == 'nan': 
                continue
            
            updates_attempted = False
            
            # --- 1. UPDATE SETTINGS TOGGLES ---
            settings_payload = {}
            if ring_col and pd.notna(row.get(ring_col)) and str(row.get(ring_col)).strip() != "":
                settings_payload["ringOnMonitoredCall"] = parse_bool(row.get(ring_col))
            if pickup_col and pd.notna(row.get(pickup_col)) and str(row.get(pickup_col)).strip() != "":
                settings_payload["pickUpCallsOnHold"] = parse_bool(row.get(pickup_col))
            if see_col and pd.notna(row.get(see_col)) and str(row.get(see_col)).strip() != "":
                settings_payload["allowSeeMyPresence"] = parse_bool(row.get(see_col))
                
            if settings_payload:
                try:
                    manager.update_presence_settings(target_id, settings_payload)
                    updates_attempted = True
                except Exception as e:
                    results["errors"].append(f"Ext {target_id}: Settings update failed - {str(e)}")

            # --- 2. SMART MERGE BLF LINES ---
            if line_ext_cols:
                try:
                    current_lines_resp = manager.get_monitored_lines(target_id) or {}
                    current_records = current_lines_resp.get('records') or []
                    
                    final_lines = {}
                    locked_lines = set()

                    for r in current_records:
                        l_id = str(r.get('id'))
                        # Null-safe extraction for all object paths
                        ext_obj = r.get('extension') or {}
                        ext_id = ext_obj.get('id')
                        
                        if ext_id:
                            final_lines[int(l_id)] = str(ext_id)
                            
                        if r.get('notEditableOnHud') is True:
                            locked_lines.add(int(l_id))

                    has_line_changes = False

                    for col in line_ext_cols:
                        val = row.get(col)
                        line_num = int(str(col).lower().replace('line', '').replace('extension', '').strip())
                        
                        if line_num in locked_lines:
                            continue
                            
                        if pd.isna(val) or str(val).strip() == "":
                            if line_num in final_lines:
                                del final_lines[line_num]
                                has_line_changes = True
                        else:
                            raw_val = str(val).split('.')[0].strip()
                            
                            if raw_val in id_set: 
                                monitored_id = raw_val
                            elif raw_val in ext_map: 
                                monitored_id = ext_map[raw_val]
                            elif raw_val.isdigit() and len(raw_val) > 5:
                                monitored_id = raw_val
                            else:
                                results["errors"].append(f"Ext {target_id}: '{raw_val}' is an invalid ID/Number. Skipping Line {line_num}.")
                                continue
                                
                            if final_lines.get(line_num) != monitored_id:
                                final_lines[line_num] = monitored_id
                                has_line_changes = True

                    if has_line_changes:
                        sorted_keys = sorted(final_lines.keys())
                        new_records = []
                        for i, k in enumerate(sorted_keys):
                            new_records.append({
                                "id": str(i + 1),
                                "extension": {"id": final_lines[k]}
                            })
                            
                        manager.update_monitored_lines(target_id, new_records)
                        updates_attempted = True
                        
                except Exception as e:
                    results["errors"].append(f"Ext {target_id}: BLF update failed - {str(e)}")

            if updates_attempted:
                results["success"] += 1
            else:
                results["errors"].append(f"Ext {target_id}: No changes detected (or lines were locked).")

        return jsonify({
            "status": "completed", 
            "message": f"Processed updates for {results['success']} users.",
            "errors": results["errors"]
        })

    except Exception as e:
        logging.error(f"Upload Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
