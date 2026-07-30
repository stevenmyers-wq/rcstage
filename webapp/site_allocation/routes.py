import io
import json
import pandas as pd
from flask import Blueprint, jsonify, request, send_file, session, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

site_allocation_bp = Blueprint('site_allocation_bp', __name__, url_prefix='/api/site_allocation')


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@site_allocation_bp.route('/sites', methods=['GET'])
@require_rc_token
def get_sites():
    """Returns the friendly Site names available on the account (for reference)."""
    token = _token()
    try:
        sites = utils.fetch_sites(token)
        names = sorted({(s.get('name') or '').strip() for s in sites if s.get('name')})
        return jsonify({"success": True, "sites": names})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@site_allocation_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Site Allocation - Template')
def download_template():
    """Builds an Extension | Site template pre-filled with current user
    extensions and their present Site, plus a reference sheet of valid Sites."""
    token = _token()
    try:
        extensions = utils.fetch_all_extensions(token)
        sites = utils.fetch_sites(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    rows = utils.build_template_rows(extensions)
    if not rows:
        rows = [{'Extension': '', 'Site': ''}]
    df = pd.DataFrame(rows, columns=['Extension', 'Site'])

    site_names = sorted({(s.get('name') or '').strip() for s in sites if s.get('name')})
    sites_df = pd.DataFrame({'Valid Site Names': site_names or ['Main Site']})

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Site Allocation')
        sites_df.to_excel(writer, index=False, sheet_name='Valid Sites')
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                sheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name='Site_Allocation_Template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@site_allocation_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Site Allocation - Update')
def upload():
    """Validates (action=preview) or applies (action=apply) the uploaded
    Extension | Site sheet, streaming per-row results as NDJSON."""
    token = _token()
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized: please bridge the connection first."}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400

    is_preview = request.form.get('action', 'preview') != 'apply'

    try:
        file = request.files['file']
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            data = io.BytesIO(file.read())
            xls = pd.ExcelFile(data)
            sheet = 'Site Allocation' if 'Site Allocation' in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet)
        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    def generate():
        try:
            for chunk in utils.process_site_batch(records, token, is_preview=is_preview):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp
