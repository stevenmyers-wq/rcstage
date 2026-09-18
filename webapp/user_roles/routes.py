import json
from flask import Blueprint, jsonify, request, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from webapp.rc_api import rc_api_call
from . import utils

user_roles_bp = Blueprint(
    'user_roles_bp', __name__,
    url_prefix='/api/user_roles'
)
user_roles_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _ndjson_stream(chunks, on_error):
    """Wrap a generator of dict chunks as a streaming NDJSON Response.

    Each chunk is emitted as one JSON line, flushed immediately so the browser
    can advance its progress bar in real time. ``on_error`` builds the error
    chunk yielded if the generator raises mid-stream."""
    def generate():
        try:
            for chunk in chunks:
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            print(on_error[1].format(e))
            yield json.dumps({"type": "error", "message": on_error[0]}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'   # disable proxy buffering so chunks flush live
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@user_roles_bp.route('/roles', methods=['GET'])
@require_rc_token
def get_roles():
    """Streams account user roles as permission-matrix rows.

    Query param ``category`` = 'all' (default) or 'custom' (custom roles only).
    """
    category = request.args.get('category', 'all').lower()
    if category not in ('all', 'custom'):
        return jsonify({"error": "category must be 'all' or 'custom'."}), 400
    return _ndjson_stream(
        utils.fetch_roles(category=category),
        ("An internal error occurred while fetching roles.", "Error fetching roles: {}"),
    )


@user_roles_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('User Roles')
def upload_roles():
    """Creates/updates custom roles from matrix rows, streaming per-row progress."""
    data = request.get_json()
    if not data or 'records' not in data:
        return jsonify({"error": "Request body must include 'records'."}), 400

    records = data['records']
    permission_columns = data.get('permissionColumns') or []
    task_id = data.get('task_id')

    if not permission_columns:
        return jsonify({"error": "Request body must include 'permissionColumns'."}), 400

    def chunks():
        try:
            yield from utils.apply_roles_from_records(records, permission_columns, task_id=task_id)
        finally:
            # Always clear the stop flag when the run ends (success, error, cancel).
            task_control.clear(task_id)

    return _ndjson_stream(
        chunks(),
        ("An internal error occurred during the update process.", "Error during upload process: {}"),
    )


# Debug passthrough is limited to role/permission endpoints so it can't be used
# as a general RC API proxy.
_DEBUG_ALLOWED = ('user-role', 'dictionary/permission')


@user_roles_bp.route('/debug', methods=['POST'])
@require_rc_token
def debug_request():
    """Send a hand-crafted request to a RingCentral role/permission endpoint and
    return the raw status and response body. Used to work out the exact request
    shape RC accepts for role permissions.

    Body: {"method": "GET|POST|PUT|DELETE", "path": "/restapi/...", "body": {..}|null}
    Only paths targeting user-role or dictionary/permission are allowed.
    """
    data = request.get_json(silent=True) or {}
    method = str(data.get('method', 'GET')).upper()
    path = str(data.get('path', '')).strip()
    body = data.get('body')

    if method not in ('GET', 'POST', 'PUT', 'DELETE'):
        return jsonify({"error": "method must be GET, POST, PUT or DELETE."}), 400
    if not path.startswith('/restapi/'):
        return jsonify({"error": "path must start with /restapi/."}), 400
    if not any(seg in path for seg in _DEBUG_ALLOWED):
        return jsonify({"error": "path must target a user-role or dictionary/permission endpoint."}), 400
    if isinstance(body, str):
        # Allow a raw JSON string body; parse it so we send real JSON.
        try:
            body = json.loads(body) if body.strip() else None
        except json.JSONDecodeError as e:
            return jsonify({"error": f"body is not valid JSON: {e}"}), 400

    kwargs = {"method": method, "return_response": True}
    if body is not None and method in ('POST', 'PUT'):
        kwargs["json"] = body

    response = rc_api_call(path, **kwargs)

    status = getattr(response, 'status_code', None)
    try:
        parsed = response.json()
    except Exception:
        parsed = getattr(response, 'text', '')

    return jsonify({
        "request": {"method": method, "path": path, "body": body},
        "status": status,
        "ok": bool(getattr(response, 'ok', False)),
        "response": parsed,
    })
