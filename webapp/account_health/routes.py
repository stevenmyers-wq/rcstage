# webapp/account_health/routes.py
import threading
from flask import Blueprint, jsonify, session
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.account_health.utils import run_discovery

account_health_bp = Blueprint(
    'account_health_bp', __name__,
    url_prefix='/api/account_health'
)

# In-memory store keyed by user email
# { email -> {"status": "running"|"done"|"error", "data": {}, "error": ""} }
_discovery_store = {}


def _run_in_background(user_email, rc_token):
    """Runs discovery in a background thread and stores result."""
    _discovery_store[user_email] = {"status": "running", "data": None, "error": None}
    try:
        result = run_discovery(rc_token)
        _discovery_store[user_email] = {"status": "done", "data": result, "error": None}
    except Exception as e:
        print(f"[account_health] Discovery failed for {user_email}: {e}")
        _discovery_store[user_email] = {"status": "error", "data": None, "error": str(e)}


@account_health_bp.route('/run', methods=['POST'])
@require_rc_token
@track_usage('Account Discovery')
def run_discovery_endpoint():
    """Kicks off background discovery for the current user."""
    user_email = session.get('user_email', 'unknown')

    # Don't re-run if already running
    existing = _discovery_store.get(user_email, {})
    if existing.get("status") == "running":
        return jsonify({"status": "already_running"}), 200

    # Extract token NOW while we still have request context
    rc_token = get_rc_access_token()

    thread = threading.Thread(
        target=_run_in_background,
        args=(user_email, rc_token),
        daemon=True
    )
    thread.start()

    return jsonify({"status": "started"}), 200


@account_health_bp.route('/status', methods=['GET'])
@require_rc_token
def get_discovery_status():
    """Returns current discovery status and data if complete."""
    user_email = session.get('user_email', 'unknown')
    store = _discovery_store.get(user_email)

    if not store:
        return jsonify({"status": "idle"}), 200

    return jsonify({
        "status": store["status"],
        "data": store.get("data"),
        "error": store.get("error"),
    }), 200


@account_health_bp.route('/clear', methods=['POST'])
@require_rc_token
def clear_discovery():
    """Clears cached results so discovery can be re-run."""
    user_email = session.get('user_email', 'unknown')
    _discovery_store.pop(user_email, None)
    return jsonify({"status": "cleared"}), 200
