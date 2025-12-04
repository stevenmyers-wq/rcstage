from flask import Blueprint, request, jsonify, send_file
# Absolute import to navigate project structure without __init__.py in subfolder
from webapp.notifications.utils import NotificationManager

# Define Blueprint
notifications_bp = Blueprint('notifications', __name__)

# Initialize logic class
manager = NotificationManager()

@notifications_bp.route('/notifications/audit', methods=['GET'])
def audit_notifications():
    try:
        # Calls the utility to generate the Excel file in memory
        output = manager.generate_audit_report()
        output.seek(0)
        
        return send_file(
            output, 
            as_attachment=True, 
            download_name='Notification_Audit.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/template', methods=['GET'])
def get_template():
    try:
        output = manager.generate_blank_template()
        output.seek(0)
        return send_file(
            output, 
            as_attachment=True, 
            download_name='Notification_Update_Template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/update', methods=['POST'])
def update_notifications():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        # Pass file stream to the utility
        logs = manager.process_update_file(file)
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
