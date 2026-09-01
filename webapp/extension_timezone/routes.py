import io
import json
import time
import threading
import pandas as pd
from flask import Blueprint, jsonify, request, send_file, Response, stream_with_context
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

extension_timezone_bp = Blueprint(
    'extension_timezone_bp', __name__, url_prefix='/api/extension_timezone'
)
extension_timezone_bp.add_url_rule(
    '/cancel', 'cancel', task_control.cancel_view, methods=['POST']
)


# ---------------------------------------------------------------------------
# Audit (streamed background job)
# ---------------------------------------------------------------------------

@extension_timezone_bp.route('/filters', methods=['GET'])
@require_rc_token
def get_filters():
    """Options for the audit's Site and Extension Type multi-select tick boxes."""
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        sites = utils.list_sites(token)
    except Exception as e:
        return jsonify({"error": f"Failed to load sites: {str(e)}"}), 500
    return jsonify({"sites": sites, "types": utils.type_filter_options()})


@extension_timezone_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Extension Timezone - Run')
def start_audit():
    token = get_rc_access_token()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    type_labels = body.get('types') or []
    site_ids = body.get('sites') or []
    if not isinstance(type_labels, list):
        type_labels = []
    if not isinstance(site_ids, list):
        site_ids = []

    task_id = f"tz_audit_{int(time.time())}"
    threading.Thread(
        target=utils.run_timezone_audit,
        args=(task_id, token, type_labels, site_ids),
        daemon=True,
    ).start()
    return jsonify({"success": True, "task_id": task_id})


@extension_timezone_bp.route('/audit/status', methods=['GET'])
def audit_status():
    task_id = request.args.get('task_id')
    data = utils.audit_progress_store.get(task_id, {})
    return jsonify({
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'file_ready': data.get('file_ready', False),
        'mismatches': data.get('mismatches', 0),
        'error': data.get('error', ''),
    })


@extension_timezone_bp.route('/audit/download', methods=['GET'])
def audit_download():
    task_id = request.args.get('task_id')
    data = utils.audit_progress_store.get(task_id, {})
    if data.get('file_ready') and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        return send_file(
            mem,
            as_attachment=True,
            download_name='Extension_Timezone_Audit.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return "File not ready or expired", 404


# ---------------------------------------------------------------------------
# Update (streamed upload -> preview / apply)
# ---------------------------------------------------------------------------

@extension_timezone_bp.route('/sheets', methods=['POST'])
def get_sheets():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files['file']
    if file.filename.endswith('.csv'):
        return jsonify(["CSV Format (No Sheets)"])
    try:
        xls = pd.ExcelFile(file)
        return jsonify(xls.sheet_names)
    except Exception as e:
        return jsonify({"error": f"Failed to parse sheets: {str(e)}"}), 400


@extension_timezone_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Extension Timezone - Update')
def upload_update():
    token = get_rc_access_token()
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400

    is_preview = request.form.get('action') == 'preview'
    sheet_name = request.form.get('sheet_name')
    task_id = request.form.get('task_id')

    try:
        file = request.files['file']
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif sheet_name and sheet_name != 'CSV Format (No Sheets)':
            df = pd.read_excel(file, sheet_name=sheet_name)
        else:
            df = pd.read_excel(file)

        if "Extension Number" not in df.columns:
            return jsonify({"type": "error",
                            "message": "File is missing required column: Extension Number."}), 400
        if "Extension Timezone" not in df.columns and "User Timezone" not in df.columns:
            return jsonify({"type": "error",
                            "message": "File is missing required column: Extension Timezone."}), 400

        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    def generate():
        try:
            for chunk in utils.update_timezone_batch(records, token, is_preview=is_preview, task_id=task_id):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp
