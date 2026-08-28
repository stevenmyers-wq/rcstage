import io
import json

import pandas as pd
from flask import (
    Blueprint, jsonify, request, send_file, session, Response, stream_with_context
)

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

directory_visibility_bp = Blueprint(
    'directory_visibility_bp', __name__, url_prefix='/api/directory_visibility'
)
directory_visibility_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@directory_visibility_bp.route('/list', methods=['GET'])
@require_rc_token
@track_usage('Directory Visibility - List')
def list_visibility():
    """Poll the connected account and return every object that supports company
    directory visibility, with its current Visible / Hidden state."""
    token = _token()
    try:
        rows, summary = utils.build_visibility_rows(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if rows is None:
        return jsonify({"error": "Could not load account extensions. Token may be invalid or expired."}), 502

    return jsonify({"records": rows, "summary": summary})


@directory_visibility_bp.route('/update', methods=['POST'])
@require_rc_token
@track_usage('Directory Visibility - Update')
def update_visibility():
    """Toggle a single object's directory visibility. Body: {id, hidden:bool}."""
    token = _token()
    data = request.get_json(silent=True) or {}
    ext_id = data.get('id')
    hidden = data.get('hidden')

    if not ext_id:
        return jsonify({"error": "Missing id"}), 400
    if not isinstance(hidden, bool):
        return jsonify({"error": "Missing or invalid 'hidden' (must be true/false)"}), 400

    try:
        ok, msg = utils.set_visibility(ext_id, hidden, token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not ok:
        return jsonify({"error": msg}), 502
    return jsonify({"success": True, "id": ext_id, "hidden": hidden, "state": msg})


@directory_visibility_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Directory Visibility - Template')
def download_template():
    """Build the bulk-update workbook, pre-filled with current state and a
    New Visibility dropdown."""
    token = _token()
    try:
        output = utils.generate_template(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return send_file(
        output,
        as_attachment=True,
        download_name='Directory_Visibility_Template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@directory_visibility_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Directory Visibility - Upload')
def upload():
    """Validate (action=preview) or apply (action=apply) the uploaded
    visibility changes, streaming per-row results as NDJSON."""
    token = _token()
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized: please bridge the connection first."}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400

    is_preview = request.form.get('action', 'preview') != 'apply'
    task_id = request.form.get('task_id')

    try:
        file = request.files['file']
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            data = io.BytesIO(file.read())
            xls = pd.ExcelFile(data)
            requested = request.form.get('sheet_name')
            if requested and requested in xls.sheet_names:
                sheet = requested
            else:
                sheet = utils.TEMPLATE_SHEET if utils.TEMPLATE_SHEET in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet)
        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    def generate():
        try:
            for chunk in utils.process_upload_batch(records, token, is_preview=is_preview, task_id=task_id):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp
