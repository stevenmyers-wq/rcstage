import io
import json

import pandas as pd
from flask import (
    Blueprint, jsonify, request, session, send_file, Response, stream_with_context
)

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

extension_activation_bp = Blueprint(
    'extension_activation_bp', __name__, url_prefix='/api/extension_activation'
)
extension_activation_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@extension_activation_bp.route('/list-extensions', methods=['GET'])
@require_rc_token
@track_usage('Extension Activation - List')
def list_extensions():
    """Enumerate every account extension for the selection table (Users, Call
    Queues, IVRs, …). The UI filters by Type / Site / current Status."""
    token = _token()
    try:
        rows, summary = utils.build_extension_rows(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if rows is None:
        return jsonify({"error": "Could not load account extensions. Token may be invalid or expired."}), 502

    return jsonify({
        "records": rows,
        "summary": summary,
        "settable_statuses": list(utils.SETTABLE_STATUSES),
    })


@extension_activation_bp.route('/update-status', methods=['POST'])
@require_rc_token
@track_usage('Extension Activation - Update')
def update_status():
    """Set a single extension's activation status. Body: {id, status}."""
    token = _token()
    data = request.get_json(silent=True) or {}
    ext_id = data.get('id')
    status = str(data.get('status', '')).strip()
    reason = str(data.get('reason', '')).strip()
    comment = str(data.get('comment', '')).strip()

    if not ext_id:
        return jsonify({"error": "Missing id"}), 400
    # Only the values RingCentral's update schema accepts (Enabled / Disabled /
    # NotActivated) are allowed here; Unassigned / Frozen are current-states
    # RingCentral manages, not write targets. RingCentral still enforces which
    # transitions are legal for each extension type and returns a specific
    # error, which is surfaced verbatim.
    if status not in utils.SETTABLE_STATUSES:
        return jsonify({
            "error": f"status must be one of {', '.join(utils.SETTABLE_STATUSES)}"
        }), 400
    # The optional suspension reason / comment only make sense when disabling.
    if reason and reason not in utils.SUSPENSION_REASONS:
        return jsonify({
            "error": f"reason must be one of {', '.join(utils.SUSPENSION_REASONS)}"
        }), 400
    if status != 'Disabled':
        reason = comment = ''

    try:
        ok, msg = utils.set_status(ext_id, status, token,
                                   reason=reason or None, comment=comment or None)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not ok:
        return jsonify({"error": msg}), 502
    return jsonify({"success": True, "id": ext_id, "status": status})


@extension_activation_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Extension Activation - Template')
def download_template():
    """Build the bulk-update workbook, pre-filled with each extension's current
    status and a New Status dropdown (Enabled / Disabled / NotActivated)."""
    token = _token()
    try:
        output = utils.generate_template(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return send_file(
        output,
        as_attachment=True,
        download_name='Extension_Activation_Template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@extension_activation_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Extension Activation - Upload')
def upload():
    """Validate (action=preview) or apply (action=apply) the uploaded activation
    changes, streaming per-row results as NDJSON."""
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
