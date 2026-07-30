import time
import threading
from flask import Blueprint, jsonify, request, send_file, session
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

user_templates_bp = Blueprint('user_templates_bp', __name__, url_prefix='/api/user_templates')

@user_templates_bp.route('/download', methods=['GET'])
@require_rc_token
@track_usage('User Templates - Download')
def download_audit():
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    try:
        excel_file = utils.generate_audit_spreadsheet(token)
        return send_file(
            excel_file,
            as_attachment=True,
            download_name='user_template_assignment.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@user_templates_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('User Templates - Upload')
def upload_audit():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    file_bytes = file.read()

    task_id = f"template_apply_{int(time.time())}"

    # Capture refresh material now (while we still have the session) so the
    # background worker can re-mint the token itself on a long run -- otherwise
    # the token expires mid-run and every call 401s "Token not found".
    auth_bundle = utils.capture_auth(session)

    thread = threading.Thread(target=utils.process_upload_background, args=(task_id, file_bytes, auth_bundle))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@user_templates_bp.route('/cancel', methods=['POST'])
@require_rc_token
def cancel_task():
    task_id = request.args.get('task_id') or (request.get_json(silent=True) or {}).get('task_id')
    data = utils.template_progress_store.get(task_id)
    if data is None:
        return jsonify({"error": "Unknown or expired task."}), 404
    # Cooperative cancel: the worker checks this flag between batches and during
    # its wait for the account lock. Batches already sent to RingCentral cannot
    # be recalled -- only not-yet-sent batches are skipped.
    data['cancel'] = True
    return jsonify({"success": True})

@user_templates_bp.route('/status', methods=['GET'])
@require_rc_token
def get_status():
    task_id = request.args.get('task_id')
    data = utils.template_progress_store.get(task_id, {})
    
    return jsonify({
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'message': data.get('message', 'Initializing...'),
        'error': data.get('error', ''),
        'results': data.get('results', [])
    })
