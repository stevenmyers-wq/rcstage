import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

@presence_bp.route('/api/presence/users', methods=['GET'])
def get_users():
    """Fetch all users to populate the selection table."""
    try:
        manager = RCPresenceManager()
        users = manager.get_all_users()
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        logging.error(f"Error fetching users for presence audit: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/audit', methods=['POST'])
def generate_audit_report():
    """Generate an XLSX report of current BLF keys for selected users."""
    try:
        data = request.json
        selected_extensions = data.get('extensions', [])
        
        if not selected_extensions:
            return jsonify({"status": "error", "message": "No extensions selected"}), 400

        manager = RCPresenceManager()
        audit_data = []

        # Iterate through selected users and fetch their BLF lines
        for ext_id in selected_extensions:
            # We need the extension number or name for reference, but the UI only sends the ID.
            # For a more complete report, you might pass the whole object from the UI.
            # Here, we assume the UI passes a list of IDs.
            lines_response = manager.get_monitored_lines(ext_id)
            records = lines_response.get('records', [])
            
            # The API states the first two lines are the user themselves and locked.
            # We will list all lines but clearly mark the locked ones.
            for i, record in enumerate(records):
                line_num = i + 1
                is_locked = "Yes" if line_num <= 2 else "No"
                monitored_ext_id = record.get('extension', {}).get('id', '')
                monitored_ext_num = record.get('extension', {}).get('extensionNumber', '')
                
                audit_data.append({
                    "Target Extension ID": ext_id,
                    "Line Number": line_num,
                    "Is Locked": is_locked,
                    "Monitored Extension ID": monitored_ext_id,
                    "Monitored Extension Number": monitored_ext_num
                })
        
        if not audit_data:
             return jsonify({"status": "error", "message": "No BLF data found for selected extensions."}), 404

        # Create DataFrame and Excel file
        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='BLF_Audit')
        
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='BLF_Presence_Audit.xlsx'
        )

    except Exception as e:
        logging.error(f"Error generating BLF audit: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def download_template():
    """Download a blank XLSX template for updating BLF keys."""
    try:
        # Create a sample dataframe
        df = pd.DataFrame({
            "Target Extension ID": ["12345678", "12345678", "87654321"],
            "Line Number": [3, 4, 3],
            "Monitored Extension ID": ["11111111", "22222222", "33333333"]
        })
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
            # You could add instructions to another sheet here if desired

        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='BLF_Update_Template.xlsx'
        )
    except Exception as e:
         return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf_from_file():
    """Process an uploaded XLSX file and update BLF keys."""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No selected file"}), 400
            
        # Read the Excel file
        df = pd.read_excel(file)
        
        # Validate required columns
        required_cols = ["Target Extension ID", "Line Number", "Monitored Extension ID"]
        for col in required_cols:
            if col not in df.columns:
                return jsonify({"status": "error", "message": f"Missing required column: {col}"}), 400

        manager = RCPresenceManager()
        results = {"success": 0, "errors": []}

        # Group operations by Target Extension ID
        # The API updates the *entire list* of BLF keys at once for a user.
        # We cannot just update line 3; we have to send the whole array.
        
        # Group by Target ID
        grouped = df.groupby("Target Extension ID")
        
        for target_id, group in grouped:
            target_id = str(int(target_id)) # Clean ID
            
            # Note: We must preserve lines 1 & 2 (the user's own lines)
            # 1. Fetch current lines
            current_lines_resp = manager.get_monitored_lines(target_id)
            current_records = current_lines_resp.get('records', [])
            
            # Initialize the new array with the required locked lines
            new_records = []
            if len(current_records) >= 2:
                new_records.append({"extension": {"id": current_records[0]['extension']['id']}})
                new_records.append({"extension": {"id": current_records[1]['extension']['id']}})
            else:
                 results["errors"].append(f"Ext {target_id}: Missing default lines 1 & 2. Cannot update safely.")
                 continue

            # 2. Add the requested lines from the spreadsheet
            # We sort by Line Number to ensure they are added in the correct order requested
            sorted_group = group.sort_values("Line Number")
            for index, row in sorted_group.iterrows():
                 monitored_id = str(int(row["Monitored Extension ID"]))
                 line_num = int(row["Line Number"])
                 
                 # Skip if they try to overwrite lines 1 or 2
                 if line_num <= 2:
                     continue
                     
                 # Add to payload
                 new_records.append({"extension": {"id": monitored_id}})

            # 3. Send the PUT request
            try:
                manager.update_monitored_lines(target_id, new_records)
                results["success"] += 1
            except Exception as e:
                results["errors"].append(f"Ext {target_id}: Update failed - {str(e)}")

        return jsonify({
            "status": "completed", 
            "message": f"Successfully updated {results['success']} users.",
            "errors": results["errors"]
        })

    except Exception as e:
        logging.error(f"Error processing BLF update: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
