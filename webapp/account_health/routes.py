# webapp/account_health/routes.py
import sys
import threading
from datetime import datetime, timezone
from flask import Blueprint, jsonify, session
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.account_health.utils import run_discovery

account_health_bp = Blueprint(
    'account_health_bp', __name__,
    url_prefix='/api/account_health'
)

# In-memory store keyed by user email
# { email -> {"status": "running"|"done"|"error", "data": {}, "error": "",
#             "logs": [{"ts": iso8601, "line": "..."}, ...]} }
_discovery_store = {}
_LOG_LIMIT = 500  # keep the most recent N log lines per user

# Maps threading.Thread ident -> user_email. Used by _TeeWriter so that
# concurrent discoveries (one per user) don't cross-contaminate logs.
_thread_to_email = {}
_thread_to_email_lock = threading.Lock()


class _TeeWriter:
    """
    File-like wrapper installed as sys.stdout during discovery runs. Every
    write goes to the real stdout (so Cloud Run / terminal logs keep working)
    AND, if the line contains '[account_health]' AND the calling thread is
    registered in _thread_to_email, the line is appended to that user's log
    list in _discovery_store.

    This is installed once, globally, and stays installed. Non-discovery
    threads (Flask request handlers, other modules) go straight through the
    real stdout because they aren't registered in _thread_to_email.
    """
    def __init__(self, original_stdout):
        self._orig = original_stdout
        # Per-thread line buffers so interleaved writes don't get spliced
        # across threads.
        self._buffers = {}
        self._buffers_lock = threading.Lock()

    def write(self, data):
        self._orig.write(data)
        tid = threading.get_ident()
        with _thread_to_email_lock:
            email = _thread_to_email.get(tid)
        if not email:
            return  # not a discovery thread, don't capture
        with self._buffers_lock:
            buf = self._buffers.get(tid, "") + data
            lines = buf.split("\n")
            self._buffers[tid] = lines[-1]  # trailing fragment
            complete_lines = lines[:-1]
        for line in complete_lines:
            line = line.rstrip()
            if not line or "[account_health]" not in line:
                continue
            entry = _discovery_store.get(email)
            if not entry:
                continue
            logs = entry.setdefault("logs", [])
            logs.append({
                "ts":   datetime.now(timezone.utc).isoformat(),
                "line": line,
            })
            if len(logs) > _LOG_LIMIT:
                del logs[:len(logs) - _LOG_LIMIT]

    def flush(self):
        self._orig.flush()


# Install the tee once at import time. Safe because:
# (a) It forwards everything to the real stdout unchanged.
# (b) It only captures lines tagged [account_health] from registered threads.
if not isinstance(sys.stdout, _TeeWriter):
    sys.stdout = _TeeWriter(sys.stdout)


def _run_in_background(user_email, rc_token, days):
    """Runs discovery in a background thread and stores result."""
    _discovery_store[user_email] = {
        "status": "running",
        "data":   None,
        "error":  None,
        "logs":   [],
    }

    # Register this thread so _TeeWriter knows which user to attribute
    # log lines to.
    tid = threading.get_ident()
    with _thread_to_email_lock:
        _thread_to_email[tid] = user_email
    try:
        result = run_discovery(rc_token, days=days)
        _discovery_store[user_email].update({
            "status": "done",
            "data":   result,
            "error":  None,
        })
    except Exception as e:
        print(f"[account_health] Discovery failed for {user_email}: {e}")
        _discovery_store[user_email].update({
            "status": "error",
            "data":   None,
            "error":  str(e),
        })
    finally:
        with _thread_to_email_lock:
            _thread_to_email.pop(tid, None)


@account_health_bp.route('/run', methods=['POST'])
@require_rc_token
@track_usage('Account Discovery')
def run_discovery_endpoint():
    """Kicks off background discovery for the current user."""
    from flask import request

    user_email = session.get('user_email', 'unknown')

    # Don't re-run if already running
    existing = _discovery_store.get(user_email, {})
    if existing.get("status") == "running":
        return jsonify({"status": "already_running"}), 200

    # Parse the requested time window (7 or 30 days). Default to 30.
    days = 30
    try:
        body = request.get_json(silent=True) or {}
        requested_days = int(body.get("days", 30))
        if requested_days in (7, 30):
            days = requested_days
    except (ValueError, TypeError):
        days = 30

    # Extract token NOW while we still have request context
    rc_token = get_rc_access_token()

    thread = threading.Thread(
        target=_run_in_background,
        args=(user_email, rc_token, days),
        daemon=True
    )
    thread.start()

    return jsonify({"status": "started"}), 200


@account_health_bp.route('/status', methods=['GET'])
@require_rc_token
def get_discovery_status():
    """Returns current discovery status, log tail, and data if complete."""
    user_email = session.get('user_email', 'unknown')
    store = _discovery_store.get(user_email)

    if not store:
        return jsonify({"status": "idle", "logs": []}), 200

    return jsonify({
        "status": store["status"],
        "data":   store.get("data"),
        "error":  store.get("error"),
        "logs":   store.get("logs", []),
    }), 200


@account_health_bp.route('/clear', methods=['POST'])
@require_rc_token
def clear_discovery():
    """Clears cached results so discovery can be re-run."""
    user_email = session.get('user_email', 'unknown')
    _discovery_store.pop(user_email, None)
    return jsonify({"status": "cleared"}), 200
