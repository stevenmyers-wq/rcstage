import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

@presence_bp.route('/api/presence/users', methods=['GET'])
def get_users():
    """Populate the UI selection table."""
    try:
        manager = RCPresenceManager()
        users = manager.get_all_users()
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        logging.error(f"Error fetching users: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/audit', methods=['POST'])
def generate_audit_report():
    """Generate the horizontal XLSX report of current BLF keys."""
    try:
        data = request.json
        selected_users = data.get('users', [])
        
        if not selected_users:
            return jsonify({"status": "error", "message": "No users selected"}), 400

        manager = RCPresenceManager()
        audit_data = []

        for user in selected_users:
            ext_id = user.get('id')
            row = {
                "Extension Name": user.get('name', ''),
                "Extension Number": user.get('extensionNumber', ''),
                "Extension ID": ext_id
            }

            lines_response = manager.get_monitored_lines(ext_id)
            records = lines_response.get('records', [])

            for i, record in enumerate(records):
                # API locks lines 1 & 2 to the user. We start mapping at Line 3.
                if i < 2: continue 
                
                line_num = i + 1
                ext_info = record.get('extension', {})
                
                # Check all button types: Try ID first, fallback to extensionNumber
                val = ext_info.get('id') or ext_info.get('extensionNumber') or ''
                row[f"Line {line_num}"] = val
                
            audit_data.append(row)
        
        # Create Excel file in memory
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
        logging.error(f"Audit Generation Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def download_template():
    """Provide a blank example template matching the desired format."""
    try:
        df = pd.DataFrame({
            "Extension Name": ["Steve Mobile", "Main Queue"],
            "Extension Number": ["11134", "11135"],
            "Extension ID": ["281658124", "281658125"],
            "Line 3": ["11116", "249339004"],
            "Line 4": ["81827", ""],
            "Line 5": ["", ""]
        })
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='BLF_Update_Template')

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
    """Process the uploaded horizontal Excel file."""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400
            
        file = request.files['file']
        df = pd.read_excel(file)
        
        if "Extension ID" not in df.columns:
            return jsonify({"status": "error", "message": "Missing required column: Extension ID"}), 400

        manager = RCPresenceManager()
        results = {"success": 0, "errors": []}

        # Identify dynamically added "Line X" columns
        line_cols = [c for c in df.columns if str(c).startswith("Line ")]
        # Sort them numerically so Line 3 comes before Line 10
        line_cols.sort(key=lambda x: int(x.split(' ')[1]) if len(x.split(' ')) > 1 and x.split(' ')[1].isdigit() else 999)

        for index, row in df.iterrows():
            target_id = str(row["Extension ID"]).split('.')[0].strip()
            if not target_id or target_id.lower() == 'nan': continue
            
            # Fetch current BLF lines to preserve the mandatory Lines 1 & 2
            current_lines_resp = manager.get_monitored_lines(target_id)
            current_records = current_lines_resp.get('records', [])
            
            new_records = []
            if len(current_records) >= 1:
                new_records.append({"extension": {"id": current_records[0].get('extension', {}).get('id')}})
            if len(current_records) >= 2:
                new_records.append({"extension": {"id": current_records[1].get('extension', {}).get('id')}})

            # Append the custom lines from the spreadsheet
            for col in line_cols:
                val = row[col]
                if pd.notna(val) and str(val).strip() != "":
                    monitored_id = str(val).split('.')[0].strip()
                    new_records.append({"extension": {"id": monitored_id}})

            try:
                manager.update_monitored_lines(target_id, new_records)
                results["success"] += 1
            except Exception as e:
                results["errors"].append(f"Ext {target_id}: Update failed - {str(e)}")

        return jsonify({
            "status": "completed", 
            "message": f"Successfully processed BLF updates for {results['success']} users.",
            "errors": results["errors"]
        })

    except Exception as e:
        logging.error(f"Error processing BLF update: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
