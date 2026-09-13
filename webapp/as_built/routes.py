# webapp/as_built/routes.py
"""
As-Built Documentation routes.

A UC (bridge) tool: the operator bridges into a customer account, selects which
item types to document and at what detail level, previews the assembled
document and downloads it as PDF or Word.

Auth: @require_rc_token — rc_api_call() automatically prefers the SM
impersonation (bridge) token in the session, so all collection runs against the
currently bridged customer account.
"""

from io import BytesIO

from flask import Blueprint, jsonify, request, send_file, session

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

as_built_bp = Blueprint('as_built_bp', __name__, url_prefix='/api/as_built')

# Last generated document per user email, so /export re-uses exactly what was
# previewed instead of re-collecting from the API.
#   { email -> {"body_html": str, "customer_name": str, "doc": {...}} }
_doc_store = {}


def _current_email():
    return session.get('user_email', 'unknown')


@as_built_bp.route('/catalog', methods=['GET'])
@require_rc_token
def catalog():
    """Return the selectable sections and detail levels for the UI."""
    return jsonify({
        "success": True,
        "sections": utils.get_catalog(),
        "detail_levels": list(utils.DETAIL_LEVELS),
    })


@as_built_bp.route('/generate', methods=['POST'])
@require_rc_token
@track_usage('As-Built Documentation')
def generate():
    """Collect the selected sections and return the assembled preview HTML."""
    data = request.get_json(silent=True) or {}
    selections = data.get('sections') or []
    customer_name = (data.get('customer_name') or '').strip()

    if not isinstance(selections, list):
        return jsonify({"success": False, "error": "Invalid section selection."}), 400

    try:
        doc = utils.collect_document(selections)
        body_html = utils.build_body_html(doc, customer_name or None)
        _doc_store[_current_email()] = {
            "body_html": body_html,
            "customer_name": customer_name or doc.get("account_name") or "Customer",
            "doc": doc,
        }
        return jsonify({
            "success": True,
            "document": body_html,
            "account_name": doc.get("account_name"),
            "section_errors": [
                {"label": s["label"], "error": s["error"]}
                for s in doc.get("sections", []) if s.get("error")
            ],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@as_built_bp.route('/export', methods=['POST'])
@require_rc_token
@track_usage('As-Built Documentation')
def export():
    """Download the last-generated document as PDF or Word."""
    data = request.get_json(silent=True) or {}
    fmt = (data.get('format') or 'pdf').strip().lower()
    if fmt not in ('pdf', 'word', 'xlsx'):
        return jsonify({"success": False, "error": "Unsupported export format."}), 400

    stored = _doc_store.get(_current_email())
    if not stored:
        return jsonify({
            "success": False,
            "error": "Nothing to export yet — generate the document first.",
        }), 400

    body_html = stored["body_html"]
    customer_name = stored["customer_name"]
    slug = utils.customer_slug(customer_name)

    try:
        if fmt == 'word':
            content = utils.build_word_bytes(body_html, customer_name)
            return send_file(
                BytesIO(content),
                mimetype='application/msword',
                as_attachment=True,
                download_name=f'As_Built_{slug}.doc',
            )
        if fmt == 'xlsx':
            content = utils.build_workbook_bytes(stored.get("doc") or {}, customer_name)
            return send_file(
                BytesIO(content),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'As_Built_{slug}.xlsx',
            )
        content = utils.build_pdf_bytes(body_html, customer_name)
        return send_file(
            BytesIO(content),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'As_Built_{slug}.pdf',
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
