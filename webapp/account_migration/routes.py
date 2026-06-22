import threading
from flask import Blueprint, jsonify, request, send_file
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from . import utils

account_migration_bp = Blueprint('account_migration_bp', __name__, url_prefix='/api/migration')

@account_migration_bp.route('/export', methods=['POST'])
@require_rc_token
@track_usage('Account Migration - Export')
def start_export():
    try:
        data = request.get_json()
        task_id = data.get('task_id')
        unbind_devices = data.get('unbind_devices', False)
        
        if not task_id:
            return jsonify({'error': 'No task ID provided'}), 400
            
        token = get_rc_access_token()
        zip_buffer = utils.run_account_export(task_id, unbind_devices, token)
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name='RC_Account_Export.zip'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@account_migration_bp.route('/import', methods=['POST'])
@require_rc_token
@track_usage('Account Migration - Import')
def start_import():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No ZIP file uploaded'}), 400
            
        file_obj = request.files['file']
        task_id = request.form.get('task_id')
        
        if not task_id:
            return jsonify({'error': 'No task ID provided'}), 400
            
        file_bytes = file_obj.read()
        token = get_rc_access_token()
        
        thread = threading.Thread(target=utils.run_account_import, args=(task_id, file_bytes, token))
        thread.start()
        
        return jsonify({'success': True, 'message': 'Import process started'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@account_migration_bp.route('/status', methods=['GET'])
@require_rc_token
def get_status():
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'current': 0, 'total': 1, 'message': 'Idle', 'status': 'idle'})
        
    progress = utils.migration_progress_store.get(task_id, {
        'current': 0, 'total': 1, 'message': 'Initializing...', 'status': 'running'
    })
    return jsonify(progress)