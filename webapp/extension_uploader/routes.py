import io
import json
import time

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


@extension_uploader_bp.route('/diagnostics', methods=['GET'])
@require_rc_token
@track_usage('Extension Uploader - Diagnostics')
def diagnostics():
    """Reads what the live account actually exposes for extension provisioning:
    the account's extension-number limits and the free (assignable) numbers per
    extension type. Optionally dry-runs a specific number+type (query params
    `ext`, `userType`) and reports whether it validates and whether it appears in
    the type's free-number pool -- to explain why createExtension may return
    EXT-250 for a specific number even though validate passed."""
    token = _token()
    if not token:
        return jsonify({"success": False, "error": "Unauthorized: please bridge the connection first."}), 401
    try:
        limits = utils.fetch_account_limits(token)
        types = []
        for label in utils.USER_TYPES:
            api_type = utils.USER_TYPE_MAP[label]
            p = utils.probe_free_numbers(token, api_type, count=20)
            types.append({
                "userType": label,
                "apiType": api_type,
                "queryable": p["ok"],
                "available": None if not p["ok"] else len(p["numbers"]),
                "sample": (p["numbers"] or [])[:20],
                "status": p["status"],
                "errorCode": p["errorCode"],
                "error": None if p["ok"] else p["error"],
            })

        result = {"success": True, "limits": limits, "types": types}

        ext = (request.args.get('ext') or '').strip()
        user_type = (request.args.get('userType') or '').strip()
        if ext and user_type in utils.USER_TYPE_MAP:
            api_type = utils.USER_TYPE_MAP[user_type]
            plan = {
                'ext': ext, 'first_name': 'Diagnostic', 'last_name': '',
                'drop_last_name': user_type in utils.FIRST_NAME_ONLY_TYPES,
                # Unique throwaway email so validate isn't rejected for a
                # duplicate; this dry-run creates nothing.
                'email': f'eu-diagnostic-{int(time.time())}@example.com',
                'department': '', 'user_type': user_type, 'api_type': api_type,
                'site_id': None,
            }
            state, msg = utils.validate_new_extension(plan, token)
            # Probe a slice of the free pool to test membership of the specific
            # number. RingCentral caps the free-numbers `count` (an oversized
            # request 400s with EXT-361), so ask for a value it accepts. If the
            # returned size is below the requested count the list is complete and
            # a "not in pool" answer is definitive.
            p = utils.probe_free_numbers(token, api_type, count=100)
            free = p["numbers"]
            result["test"] = {
                "ext": ext,
                "userType": user_type,
                "apiType": api_type,
                "validate": state,
                "message": msg,
                "freePoolQueryable": p["ok"],
                "freePoolStatus": p["status"],
                "freePoolError": None if p["ok"] else (p["errorCode"] or p["error"]),
                "freePoolSize": None if free is None else len(free),
                "inFreePool": (free is not None) and (ext in free),
            }
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
