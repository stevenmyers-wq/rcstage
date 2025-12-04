from flask import Blueprint, request, jsonify, send_file, session
# Absolute import to navigate project structure without __init__.py in subfolder
from webapp.notifications.utils import NotificationManager

# Define Blueprint
notifications_bp = Blueprint('notifications', __name__)

# Initialize logic class
manager = NotificationManager()

@notifications_bp.route('/notifications/audit', methods=['GET'])
def audit_notifications():
    try:
        # Get the token from the current session
        token = session.get('rc_access_token')
        
        # Pass the token to the utility so it can use it in background/threads
        output = manager.generate_audit_report(token=token)
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
        # Get the token for the update process as well
        token = session.get('rc_access_token')

        # Pass file stream and token to the utility
        logs = manager.process_update_file(file, token=token)
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
