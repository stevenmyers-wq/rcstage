import threading
import time
from io import BytesIO

from flask import (Blueprint, current_app, jsonify, request, send_file,
                   session)
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

account_migration_bp = Blueprint('account_migration_bp', __name__, url_prefix='/api/migration')
account_migration_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _current_email():
    return session.get('user_email', 'unknown')


def _session_auth_data():
    """Snapshot the auth material a background job needs to keep calling RC after
    the request (and its session) is gone, including the SM bridge refresh path.
    Mirrors Device Ringing Audit / As-Built."""
    return {
        'access_token': get_rc_access_token(),
        'refresh_token': session.get('rc_refresh_token'),
        'client_id': session.get('rc_current_client_id'),
        'server_url': current_app.config.get(
            'RC_SERVER_URL', 'https://platform.ringcentral.com'),
        'sm_employee_token': session.get('sm_employee_token'),
        'sm_employee_refresh_token': session.get('sm_employee_refresh_token'),
        'sm_target_id': session.get('sm_target_id'),
    }


def _start(kind, task_id, target, args):
    """Seed the progress store and launch a background daemon thread."""
    utils.migration_progress_store[task_id] = {
        'current': 0, 'total': 100, 'message': 'Starting…',
        'status': 'running', 'results': [], 'kind': kind,
    }
    app = current_app._get_current_object()
    thread = threading.Thread(target=target, args=(app,) + args)
    thread.daemon = True
    thread.start()


@account_migration_bp.route('/export', methods=['POST'])
@require_rc_token
@track_usage('Account Migration - Export')
def start_export():
    """Start a background export. The browser polls /status, then downloads via
    /result/download once complete — keeping each request short so a large
    account can't overrun the Cloud Run request timeout."""
    data = request.get_json() or {}
    task_id = data.get('task_id')
    unbind_devices = data.get('unbind_devices', False)
    if not task_id:
        return jsonify({'error': 'No task ID provided'}), 400

    auth_data = _session_auth_data()
    if not auth_data.get('access_token'):
        return jsonify({'error': 'Unauthorized'}), 401

    _start('export', task_id, utils.run_export_background,
           (task_id, unbind_devices, auth_data, _current_email()))
    return jsonify({'success': True, 'task_id': task_id})


@account_migration_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Account Migration - Audit')
def start_audit():
    """Start a background reader-friendly audit; poll /status then download."""
    data = request.get_json() or {}
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'error': 'No task ID provided'}), 400

    auth_data = _session_auth_data()
    if not auth_data.get('access_token'):
        return jsonify({'error': 'Unauthorized'}), 401

    _start('audit', task_id, utils.run_audit_background,
           (task_id, auth_data, _current_email()))
    return jsonify({'success': True, 'task_id': task_id})


@account_migration_bp.route('/import', methods=['POST'])
@require_rc_token
@track_usage('Account Migration - Import')
def start_import():
    if 'file' not in request.files:
        return jsonify({'error': 'No ZIP file uploaded'}), 400

    file_obj = request.files['file']
    task_id = request.form.get('task_id')
    if not task_id:
        return jsonify({'error': 'No task ID provided'}), 400

    auth_data = _session_auth_data()
    if not auth_data.get('access_token'):
        return jsonify({'error': 'Unauthorized'}), 401

    file_bytes = file_obj.read()
    _start('import', task_id, utils.run_import_background,
           (task_id, file_bytes, auth_data, _current_email()))
    return jsonify({'success': True, 'message': 'Import process started'})


@account_migration_bp.route('/status', methods=['GET'])
@require_rc_token
def get_status():
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'current': 0, 'total': 1, 'message': 'Idle', 'status': 'idle'})

    progress = utils.migration_progress_store.get(task_id)
    if progress is None:
        # Not in this instance's memory — resolve a finished run from durable
        # storage so a poll that landed elsewhere (or after a restart) still works.
        from . import storage
        rec = storage.get_record(task_id)
        if rec and rec.get('status') == 'completed':
            return jsonify({'current': 100, 'total': 100, 'status': 'completed',
                            'message': 'Complete.', 'results': [],
                            'download_ready': True})
        if rec and rec.get('status') == 'error':
            return jsonify({'current': 0, 'total': 100, 'status': 'error',
                            'message': rec.get('error') or 'Failed.', 'results': []})
        return jsonify({'current': 0, 'total': 1, 'message': 'Initializing...',
                        'status': 'running', 'results': []})

    return jsonify({
        'current': progress.get('current', 0),
        'total': progress.get('total', 1),
        'message': progress.get('message', 'Initializing...'),
        'status': progress.get('status', 'running'),
        'results': progress.get('results', []),
        # True once a downloadable artifact exists for this task.
        'download_ready': bool(progress.get('file_data')),
        'download_name': progress.get('download_name'),
    })


@account_migration_bp.route('/result/download', methods=['GET'])
@require_rc_token
def result_download():
    """Download a finished export/audit/import artifact — from memory if this
    instance produced it, else from durable storage."""
    task_id = request.args.get('task_id') or ''
    entry = utils.migration_progress_store.get(task_id) or {}
    data = entry.get('file_data')
    if data:
        return send_file(
            BytesIO(data),
            mimetype=entry.get('content_type', 'application/octet-stream'),
            as_attachment=True,
            download_name=entry.get('download_name', f'{task_id}.bin'))

    from . import storage
    data, filename, ctype = storage.load_file(task_id)
    if not data:
        return jsonify({'error': 'That file is no longer available.'}), 404
    return send_file(BytesIO(data), mimetype=ctype, as_attachment=True,
                     download_name=filename)


@account_migration_bp.route('/history', methods=['GET'])
@require_rc_token
def history():
    """List the current user's recent migration outputs (last 7 days)."""
    from . import storage
    return jsonify({
        'success': True,
        'enabled': storage.storage_enabled(),
        'items': storage.list_recent(_current_email()),
    })
