from flask import Blueprint, request, jsonify, send_file, session, send_from_directory
import os
from webapp.notifications.utils import NotificationManager, REPORT_DIR

notifications_bp = Blueprint('notifications', __name__)
manager = NotificationManager()

# 1. Start the Job
@notifications_bp.route('/notifications/audit/start', methods=['POST'])
def start_audit():
    try:
        token = session.get('rc_access_token')
        job_id = manager.start_audit_job(token)
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 2. Check Status
@notifications_bp.route('/notifications/audit/status/<job_id>', methods=['GET'])
def check_audit_status(job_id):
    try:
        status = manager.get_job_status(job_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 3. Download Result
@notifications_bp.route('/notifications/audit/download/<filename>', methods=['GET'])
def download_audit_result(filename):
    try:
        return send_from_directory(
            os.path.abspath(REPORT_DIR), 
            filename, 
            as_attachment=True
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 404

# ... (Keep template and update routes same as before) ...
