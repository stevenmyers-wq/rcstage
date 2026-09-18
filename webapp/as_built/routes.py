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

import threading
import time
from io import BytesIO

from flask import (Blueprint, current_app, jsonify, request, send_file,
                   session)

from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from . import utils

as_built_bp = Blueprint('as_built_bp', __name__, url_prefix='/api/as_built')

# Last generated document per user email, so /export re-uses exactly what was
# previewed instead of re-collecting from the API.
#   { email -> {"body_html": str, "customer_name": str, "doc": {...}} }
_doc_store = {}


def _current_email():
    return session.get('user_email', 'unknown')


def _session_auth_data():
    """Snapshot the auth material the background generation thread needs to keep
    calling RC after the request (and its session) is gone — including the SM
    bridge refresh path. Mirrors Device Ringing Audit / Deskphone Ring Time."""
    return {
        'access_token': get_rc_access_token(),
        'refresh_token': session.get('rc_refresh_token'),
        'client_id': session.get('rc_current_client_id'),
        'server_url': current_app.config.get(
            'RC_SERVER_URL', 'https://platform.ringcentral.com'),
        'sm_employee_token': session.get('sm_employee_token'),
        'sm_employee_refresh_token': session.get('sm_employee_refresh_token'),
        'sm_target_id': session.get('sm_target_id'),
    }


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
    """Kick off document collection in a background thread and return a task id.

    The browser then polls /generate/status. Keeping each HTTP request short
    means a large account can't overrun the Cloud Run request timeout (which
    surfaced as a bare "Network error during generation."), and the SM bridge is
    kept alive by the background thread's self-healing token for the whole run.
    """
    data = request.get_json(silent=True) or {}
    selections = data.get('sections') or []
    customer_name = (data.get('customer_name') or '').strip()

    if not isinstance(selections, list):
        return jsonify({"success": False, "error": "Invalid section selection."}), 400

    auth_data = _session_auth_data()
    if not auth_data.get('access_token'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    task_id = f"asbuilt_{_current_email()}_{int(time.time() * 1000)}"
    utils._gen_store[task_id] = {
        "status": "running", "message": "Starting…", "error": None,
        "body_html": None, "account_name": None,
        "customer_name": customer_name, "doc": None, "section_errors": [],
    }

    # Pass the real app object so the thread can push an app context.
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=utils.run_generation_background,
        args=(app, task_id, selections, customer_name, auth_data,
              _current_email()))
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "task_id": task_id})


def _section_errors(doc):
    return [{"label": s["label"], "error": s["error"]}
            for s in (doc or {}).get("sections", []) if s.get("error")]


def _store_for_export(bundle):
    """Put a result bundle into _doc_store so /export can serve any format."""
    doc = bundle.get("doc") or {}
    _doc_store[_current_email()] = {
        "body_html": bundle.get("body_html"),
        "customer_name": (bundle.get("customer_name")
                          or doc.get("account_name") or "Customer"),
        "doc": doc,
    }


@as_built_bp.route('/generate/status', methods=['GET'])
@require_rc_token
def generate_status():
    """Poll a background generation task. On completion, hands the finished
    document to /export (keyed by user email) and returns the preview HTML."""
    from . import storage

    task_id = request.args.get('task_id') or ''
    store = utils._gen_store.get(task_id)

    if not store:
        # The task isn't in this instance's memory — the poll may have landed on
        # a different Cloud Run instance, or the container recycled. Fall back to
        # durable storage: a completed doc is still fully recoverable there.
        bundle = storage.load_document(task_id)
        if bundle:
            _store_for_export(bundle)
            return jsonify({
                "success": True, "status": "completed",
                "document": bundle.get("body_html"),
                "account_name": (bundle.get("doc") or {}).get("account_name"),
                "section_errors": _section_errors(bundle.get("doc")),
            })
        rec = storage.get_record(task_id)
        if rec and rec.get("status") == "error":
            return jsonify({"success": False, "status": "error",
                            "error": rec.get("error") or "Generation failed."}), 500
        if rec and rec.get("status") == "running":
            return jsonify({"success": True, "status": "running",
                            "message": "Working…"})
        return jsonify({"success": False, "status": "error",
                        "error": "Unknown or expired task — please generate again."}), 404

    status = store.get("status")
    if status == "completed":
        _store_for_export({
            "body_html": store.get("body_html"),
            "customer_name": store.get("customer_name") or store.get("account_name"),
            "doc": store.get("doc") or {},
        })
        payload = {
            "success": True,
            "status": "completed",
            "document": store.get("body_html"),
            "account_name": store.get("account_name"),
            "section_errors": store.get("section_errors") or [],
        }
        # Free the (potentially large) buffer now it lives in _doc_store.
        utils._gen_store.pop(task_id, None)
        return jsonify(payload)

    if status == "error":
        err = store.get("error") or "Generation failed."
        utils._gen_store.pop(task_id, None)
        return jsonify({"success": False, "status": "error", "error": err}), 500

    return jsonify({
        "success": True,
        "status": "running",
        "message": store.get("message") or "Collecting configuration from the account…",
    })


@as_built_bp.route('/history', methods=['GET'])
@require_rc_token
def history():
    """List the current user's stored As-Built documents (last 7 days)."""
    from . import storage
    return jsonify({
        "success": True,
        "enabled": storage.storage_enabled(),
        "items": storage.list_recent(_current_email()),
    })


@as_built_bp.route('/history/load', methods=['GET'])
@require_rc_token
def history_load():
    """Load a stored As-Built into the preview and arm /export for it."""
    from . import storage
    task_id = request.args.get('task_id') or ''
    bundle = storage.load_document(task_id)
    if not bundle:
        return jsonify({"success": False,
                        "error": "That document is no longer available."}), 404
    _store_for_export(bundle)
    doc = bundle.get("doc") or {}
    return jsonify({
        "success": True,
        "document": bundle.get("body_html"),
        "account_name": doc.get("account_name"),
        "section_errors": _section_errors(doc),
    })


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
