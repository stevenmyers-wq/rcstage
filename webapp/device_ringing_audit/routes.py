import time
import threading
import io
from datetime import datetime
from flask import Blueprint, jsonify, send_file, request
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from .utils import run_audit_background, audit_progress_store

device_ringing_audit_bp = Blueprint('device_ringing_audit_bp', __name__, url_prefix='/api/device_ringing_audit')

@device_ringing_audit_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Device Ringing Audit')
def start_audit():
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    
    task_id = f"ringing_audit_{int(time.time())}"
    
    # Start the audit in a background thread
    thread = threading.Thread(target=run_audit_background, args=(task_id, token))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@device_ringing_audit_bp.route('/audit/status', methods=['GET'])
@require_rc_token
def audit_status():
    task_id = request.args.get('task_id')
    data = audit_progress_store.get(task_id, {})
    
    safe_data = {
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'message': data.get('message', 'Initializing...'),
        'error': data.get('error', '')
    }
    return jsonify(safe_data)

@device_ringing_audit_bp.route('/audit/download', methods=['GET'])
@require_rc_token
def audit_download():
    task_id = request.args.get('task_id')
    data = audit_progress_store.get(task_id, {})
    
    if data.get('status') == 'completed' and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        filename = f"Device_Ringing_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return send_file(
            mem, 
            as_attachment=True, 
            download_name=filename, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return "File not ready or expired", 404
