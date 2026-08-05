import io
import time
import threading

from flask import Blueprint, jsonify, request, session, send_file

from webapp.usage_tracking import track_usage
from . import utils

user_status_bp = Blueprint('user_status', __name__, url_prefix='/api/user_status')


def _require_bridge():
    """Return the customer-scoped bridge token, or None if not bridged."""
    return session.get('sm_isolated_token')


def _start_task(target, *args):
    """Spin up a background worker and return its task_id. The bridge token is
    captured here (request context) and handed to the thread, which has no
    session of its own."""
    task_id = f"{target.__name__}_{int(time.time() * 1000)}"
    threading.Thread(target=target, args=(task_id, *args), daemon=True).start()
    return task_id


@user_status_bp.route('/queues', methods=['GET'])
def get_queues():
    token = _require_bridge()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    try:
        return jsonify({"success": True, "queues": utils.fetch_all_queues(token)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- User DND Status --------------------------------------------------------
@user_status_bp.route('/dnd/audit', methods=['POST'])
@track_usage('User Status DND Audit')
def dnd_audit():
    token = _require_bridge()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    task_id = _start_task(utils.run_dnd_audit, token)
    return jsonify({"success": True, "task_id": task_id})


@user_status_bp.route('/dnd/reset', methods=['POST'])
@track_usage('User Status DND Reset')
def dnd_reset():
    token = _require_bridge()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    task_id = _start_task(utils.run_dnd_reset, token)
    return jsonify({"success": True, "task_id": task_id})


# --- Call Queue Status ------------------------------------------------------
@user_status_bp.route('/cq/audit', methods=['POST'])
@track_usage('User Status Queue Audit')
def cq_audit():
    token = _require_bridge()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    queue_ids = (request.json or {}).get('queue_ids') or None
    task_id = _start_task(utils.run_cq_audit, token, queue_ids)
    return jsonify({"success": True, "task_id": task_id})


@user_status_bp.route('/cq/enable', methods=['POST'])
@track_usage('User Status Queue Enable')
def cq_enable():
    token = _require_bridge()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    queue_ids = (request.json or {}).get('queue_ids') or None
    task_id = _start_task(utils.run_cq_enable, token, queue_ids)
    return jsonify({"success": True, "task_id": task_id})


# --- Shared progress + download ---------------------------------------------
@user_status_bp.route('/status', methods=['GET'])
def task_status():
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    return jsonify({
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'unknown'),
        'file_ready': data.get('file_ready', False),
        'error': data.get('error', ''),
        'summary': data.get('summary', ''),
    })


@user_status_bp.route('/results', methods=['GET'])
def task_results():
    """Raw rows for a completed task, for the in-app live view table."""
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    if data.get('status') == 'completed':
        return jsonify({
            "success": True,
            "rows": data.get('rows', []),
            "columns": data.get('columns', []),
            "summary": data.get('summary', ''),
        })
    if data.get('status') == 'error':
        return jsonify({"success": False, "error": data.get('error', 'Task failed.')}), 500
    return jsonify({"success": False, "error": "Results not ready."}), 404


@user_status_bp.route('/download', methods=['GET'])
def task_download():
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    if data.get('file_ready') and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        name = 'User_Status_Queue' if task_id.startswith('run_cq') else 'User_Status_DND'
        return send_file(
            mem,
            as_attachment=True,
            download_name=f'{name}_Report.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    return "File not ready or expired", 404
