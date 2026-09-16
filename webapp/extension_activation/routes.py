from flask import Blueprint, jsonify, request, session

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

extension_activation_bp = Blueprint(
    'extension_activation_bp', __name__, url_prefix='/api/extension_activation'
)


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

    if not ext_id:
        return jsonify({"error": "Missing id"}), 400
    # Only the administrator-settable activation states are accepted here;
    # NotActivated / Unassigned / Frozen are current-states RingCentral manages,
    # not write targets. RingCentral still enforces which transitions are legal
    # for each extension type and returns a specific error, which is surfaced
    # verbatim.
    if status not in utils.SETTABLE_STATUSES:
        return jsonify({
            "error": f"status must be one of {', '.join(utils.SETTABLE_STATUSES)}"
        }), 400

    try:
        ok, msg = utils.set_status(ext_id, status, token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not ok:
        return jsonify({"error": msg}), 502
    return jsonify({"success": True, "id": ext_id, "status": status})
