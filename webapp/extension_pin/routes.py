from flask import Blueprint, jsonify, request, session

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

extension_pin_bp = Blueprint(
    'extension_pin_bp', __name__, url_prefix='/api/extension_pin'
)


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@extension_pin_bp.route('/list-extensions', methods=['GET'])
@require_rc_token
@track_usage('Extension PIN - List')
def list_extensions():
    """Enumerate every account extension for the selection table (Users, Call
    Queues, IVRs, …). The UI filters by Type / Site."""
    token = _token()
    try:
        rows, summary = utils.build_extension_rows(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if rows is None:
        return jsonify({"error": "Could not load account extensions. Token may be invalid or expired."}), 502

    return jsonify({"records": rows, "summary": summary})


@extension_pin_bp.route('/update-pin', methods=['POST'])
@require_rc_token
@track_usage('Extension PIN - Update')
def update_pin():
    """Set a single extension's mailbox PIN. Body: {id, pin}."""
    token = _token()
    data = request.get_json(silent=True) or {}
    ext_id = data.get('id')
    pin = str(data.get('pin', '')).strip()

    if not ext_id:
        return jsonify({"error": "Missing id"}), 400
    # The mailbox PIN is numeric; RingCentral enforces the length / complexity
    # rules for the account, so only the basic shape is checked here and RC's
    # own error is surfaced for anything it rejects.
    if not pin.isdigit():
        return jsonify({"error": "PIN must contain digits only"}), 400

    try:
        ok, msg = utils.set_pin(ext_id, pin, token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not ok:
        return jsonify({"error": msg}), 502
    return jsonify({"success": True, "id": ext_id})
