import io
import json

import pandas as pd
from flask import (
    Blueprint, jsonify, request, send_file, Response, stream_with_context
)

from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

caller_id_bp = Blueprint('caller_id_bp', __name__, url_prefix='/api/caller_id')
caller_id_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _ndjson(generator_factory):
    """Wrap a chunk generator as a streamed NDJSON response."""
    def generate():
        try:
            for chunk in generator_factory():
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp


# ---------------------------------------------------------------------------
# Data for the in-tool selection (users, sites, caller ID numbers)
# ---------------------------------------------------------------------------

@caller_id_bp.route('/extensions', methods=['GET'])
@require_rc_token
@track_usage('Caller ID - List')
def list_extensions():
    """Return the User-family extensions (for the tick list) plus the account's
    sites and available caller ID numbers (for the filters and CLI picker)."""
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        users = utils.list_user_extensions(token)
        sites = utils.list_sites(token)
        numbers = utils.list_caller_id_numbers(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"records": users, "sites": sites, "numbers": numbers})


# ---------------------------------------------------------------------------
# In-tool selection: apply one chosen number to the ticked users
# ---------------------------------------------------------------------------

@caller_id_bp.route('/apply', methods=['POST'])
@require_rc_token
@track_usage('Caller ID - Apply')
def apply_selection():
    token = get_rc_access_token()
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    targets = body.get('targets') or []
    number_id = body.get('numberId')
    is_preview = body.get('action', 'preview') != 'apply'
    task_id = body.get('task_id')

    if not isinstance(targets, list) or not targets:
        return jsonify({"type": "error", "message": "Select at least one user."}), 400
    if not number_id:
        return jsonify({"type": "error", "message": "Select a caller ID number to apply."}), 400

    return _ndjson(lambda: utils.apply_selection_batch(
        targets, token, number_id, is_preview=is_preview, task_id=task_id
    ))


# ---------------------------------------------------------------------------
# Bulk template: download / upload (preview / apply)
# ---------------------------------------------------------------------------

@caller_id_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Caller ID - Template')
def download_template():
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        output = utils.generate_template(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return send_file(
        output,
        as_attachment=True,
        download_name='Caller_ID_Template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@caller_id_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Caller ID - Upload')
def upload():
    token = get_rc_access_token()
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400

    is_preview = request.form.get('action', 'preview') != 'apply'
    task_id = request.form.get('task_id')

    try:
        file = request.files['file']
        if (file.filename or '').lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            data = io.BytesIO(file.read())
            xls = pd.ExcelFile(data)
            requested = request.form.get('sheet_name')
            if requested and requested in xls.sheet_names:
                sheet = requested
            elif utils.TEMPLATE_SHEET in xls.sheet_names:
                sheet = utils.TEMPLATE_SHEET
            else:
                sheet = xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet)

        if 'Extension Number' not in df.columns:
            return jsonify({"type": "error",
                            "message": "File is missing required column: Extension Number."}), 400
        if 'New Caller ID' not in df.columns:
            return jsonify({"type": "error",
                            "message": "File is missing required column: New Caller ID."}), 400

        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    return _ndjson(lambda: utils.apply_upload_batch(
        records, token, is_preview=is_preview, task_id=task_id
    ))
