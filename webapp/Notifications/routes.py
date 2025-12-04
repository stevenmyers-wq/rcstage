from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from notification_service import NotificationManager
import os
import io

notification_bp = Blueprint('notification_bp', __name__)

# Initialize logic class
manager = NotificationManager()

@notification_bp.route('/api/notifications/audit', methods=['GET'])
def audit_notifications():
    try:
        # Generates Excel file in memory
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

@notification_bp.route('/api/notifications/template', methods=['GET'])
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

@notification_bp.route('/api/notifications/update', methods=['POST'])
def update_notifications():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        # Pass file stream directly to manager
        logs = manager.process_update_file(file)
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
