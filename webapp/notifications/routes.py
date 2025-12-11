from flask import Blueprint, request, jsonify, send_file, session, send_from_directory
import os
import io
import pandas as pd
from webapp.notifications.utils import NotificationManager, REPORT_DIR

notifications_bp = Blueprint('notifications', __name__)
manager = NotificationManager()

# --- AUDIT ROUTES ---

@notifications_bp.route('/notifications/audit/start', methods=['POST'])
def start_audit():
    try:
        token = session.get('rc_access_token')
        job_id = manager.start_audit_job(token)
        return jsonify({"job_id": job_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/audit/status/<job_id>', methods=['GET'])
def check_job_status(job_id):
    """Generic status checker for both Audit and Update jobs."""
    try:
        status = manager.get_job_status(job_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@notifications_bp.route('/notifications/audit/download/<filename>', methods=['GET'])
def download_audit_result(filename):
    try:
        return send_from_directory(os.path.abspath(REPORT_DIR), filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

# --- UPDATE ROUTES ---

@notifications_bp.route('/notifications/update', methods=['POST'])
def update_notifications():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        # Read file into DataFrame immediately (Synchronous)
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
            
        token = session.get('rc_access_token')
        
        # Start Background Job
        job_id = manager.start_update_job(df, token)
        
        return jsonify({"job_id": job_id, "status": "started"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- TEMPLATE ROUTE ---

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
