import io
import time
import threading

from flask import Blueprint, jsonify, request, send_file, session

from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

ai_note_manager_bp = Blueprint('ai_note_manager', __name__, url_prefix='/api/ai_note_manager')
ai_note_manager_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


@ai_note_manager_bp.route('/users', methods=['GET'])
def get_users():
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    try:
        users = utils.fetch_all_users(token)
        return jsonify({"success": True, "users": users})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_note_manager_bp.route('/collect', methods=['POST'])
@track_usage('AI Note Manager Collect')
def collect():
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.json or {}
    ext_ids = data.get('extension_ids', [])
    date_from = data.get('date_from')
    date_to = data.get('date_to')

    if not ext_ids:
        return jsonify({"success": False, "error": "No users selected."}), 400
    if not date_from or not date_to:
        return jsonify({"success": False, "error": "A start and end date are required."}), 400

    task_id = f"ainotes_{int(time.time())}"
    threading.Thread(
        target=utils.run_collection,
        args=(task_id, ext_ids, date_from, date_to, token),
        daemon=True,
    ).start()

    return jsonify({"success": True, "task_id": task_id})


@ai_note_manager_bp.route('/status', methods=['GET'])
def status():
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    return jsonify({
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'file_ready': data.get('file_ready', False),
        'error': data.get('error', ''),
        'summary': data.get('summary', ''),
    })


@ai_note_manager_bp.route('/results', methods=['GET'])
def results():
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    if data.get('status') != 'completed':
        return jsonify({"success": False, "error": "Results not ready."}), 404
    return jsonify({
        "success": True,
        "columns": data.get('columns', []),
        "rows": data.get('rows', []),
        "summary": data.get('summary', ''),
    })


@ai_note_manager_bp.route('/download', methods=['GET'])
def download():
    task_id = request.args.get('task_id')
    data = utils.progress_store.get(task_id, {})
    if data.get('file_ready') and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        return send_file(
            mem,
            as_attachment=True,
            download_name='AI_Notes_Export.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    return "File not ready or expired", 404
