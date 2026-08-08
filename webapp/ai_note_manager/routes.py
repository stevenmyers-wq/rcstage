import io
import time
import threading

from flask import Blueprint, jsonify, request, send_file, session

from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

ai_note_manager_bp = Blueprint('ai_note_manager', __name__, url_prefix='/api/ai_note_manager')
ai_note_manager_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])

# Unlike the other UC tools, AI Note Manager does NOT use the SM impersonation
# bridge. The ai-notes/search endpoint requires the AllInternal permission
# scope, which the bridged partner token does not carry. So this module reads
# the standard RingEX PKCE token (`rc_access_token`) directly — the user must
# connect via the "REX Authentication" tab with a client ID that holds the
# AllInternal scope. We deliberately do NOT fall back to `sm_isolated_token`.
AUTH_HINT = "RingEX authentication required. Connect on the REX Authentication tab (the client must hold the AllInternal scope)."


@ai_note_manager_bp.route('/debug', methods=['GET'])
def debug():
    """Diagnostic: returns raw RingCentral call-log + ai-notes/search responses
    for the CALLER'S OWN extension, straight to the browser. Visit
    /api/ai_note_manager/debug while REX-connected to inspect the real response
    shapes (bypasses Cloud Run app-logger level). Optional ?date_from&date_to
    (YYYY-MM-DD)."""
    token = session.get('rc_access_token')
    if not token:
        return jsonify({"error": AUTH_HINT}), 401
    try:
        out = utils.debug_probe(
            token,
            date_from=request.args.get('date_from'),
            date_to=request.args.get('date_to'),
        )
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_note_manager_bp.route('/users', methods=['GET'])
def get_users():
    token = session.get('rc_access_token')
    if not token:
        return jsonify({"success": False, "error": AUTH_HINT}), 401
    try:
        users = utils.fetch_all_users(token)
        return jsonify({"success": True, "users": users})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_note_manager_bp.route('/collect', methods=['POST'])
@track_usage('AI Note Manager Collect')
def collect():
    token = session.get('rc_access_token')
    if not token:
        return jsonify({"success": False, "error": AUTH_HINT}), 401

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
