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

extension_uploader_bp = Blueprint('extension_uploader_bp', __name__, url_prefix='/api/extension_uploader')
extension_uploader_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@extension_uploader_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Extension Uploader - Template')
def download_template():
    """Builds the blank upload workbook with live Site / Role dropdowns."""
    token = _token()
    try:
        output = utils.generate_template(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return send_file(
        output,
        as_attachment=True,
        download_name='Extension_Uploader_Template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@extension_uploader_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Extension Uploader - Upload')
def upload():
    """Validates (action=preview) or creates (action=apply) the uploaded
    extensions, streaming per-row results as NDJSON."""
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
