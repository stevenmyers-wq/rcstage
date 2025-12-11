from flask import Blueprint, request, jsonify, send_file, session, send_from_directory
import os
import io
from webapp.notifications.utils import NotificationManager, REPORT_DIR

notifications_bp = Blueprint('notifications', __name__)
manager = NotificationManager()

# ==========================================
# 1. AUDIT (Background Job System)
# ==========================================

@notifications_bp.route('/notifications/audit/start', methods=['POST'])
def start_audit():
    try:
        token = session.get('rc_access_token')
        # Start the background thread
        job_id = manager.start_audit_job(token)
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/audit/status/<job_id>', methods=['GET'])
def check_audit_status(job_id):
    try:
        status = manager.get_job_status(job_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/audit/download/<filename>', methods=['GET'])
def download_audit_result(filename):
    try:
        # Securely serve the file from the static/reports directory
        return send_from_directory(
            os.path.abspath(REPORT_DIR), 
            filename, 
            as_attachment=True
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 404


# ==========================================
# 2. TEMPLATE (Download Blank Excel)
# ==========================================

@notifications_bp.route('/notifications/template', methods=['GET'])
def get_template():
    try:
        # Generate the BytesIO object from Utils
        output = manager.generate_blank_template()
        
        # IMPORTANT: Reset pointer to start of file before sending
        output.seek(0)
        
        return send_file(
            output, 
            as_attachment=True, 
            download_name='Notification_Update_Template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================================
# 3. UPDATE / CREATE (Upload Excel)
# ==========================================

@notifications_bp.route('/notifications/update', methods=['POST'])
def update_notifications():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        token = session.get('rc_access_token')
        # Process the uploaded file
        logs = manager.process_update_file(file, token=token)
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
