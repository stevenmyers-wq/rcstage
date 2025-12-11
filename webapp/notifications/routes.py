from flask import Blueprint, request, jsonify, send_file, session, Response, stream_with_context
from webapp.notifications.utils import NotificationManager

notifications_bp = Blueprint('notifications', __name__)
manager = NotificationManager()

@notifications_bp.route('/notifications/audit', methods=['GET'])
def audit_notifications():
    try:
        token = session.get('rc_access_token')
        
        # Use stream_with_context to keep connection open
        return Response(
            stream_with_context(manager.generate_audit_csv_stream(token=token)),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=Notification_Audit.csv'}
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
        token = session.get('rc_access_token')
        # Note: Ensure you pasted the full process_update_file logic into utils.py
        logs = manager.process_update_file(file, token=token)
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
