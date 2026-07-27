from functools import wraps
from flask import Blueprint, jsonify, request

from webapp.auth_utils import is_authenticated
from webapp.usage_tracking import track_usage
from . import utils

network_requirements_bp = Blueprint(
    'network_requirements_bp', __name__, url_prefix='/api/network_requirements'
)


def require_login(f):
    """Website (Google SSO) auth only. This tool makes no RingCentral API calls,
    so it does not need an RC token or an SM bridge — just a logged-in user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"success": False, "error": "Login required."}), 401
        return f(*args, **kwargs)
    return decorated


@network_requirements_bp.route('/catalog', methods=['GET'])
@require_login
def api_catalog():
    """Return the selectable services, desk-phone vendors and integrations."""
    return jsonify({"success": True, "catalog": utils.get_catalog()})


@network_requirements_bp.route('/generate', methods=['POST'])
@require_login
@track_usage('Network Requirements Generator')
def api_generate():
    """Assemble the customer-specific network requirements document."""
    data = request.get_json(silent=True) or {}

    services = data.get('services') or []
    integrations = data.get('integrations') or []
    if not isinstance(services, list) or not isinstance(integrations, list):
        return jsonify({"success": False, "error": "Invalid selection payload."}), 400

    try:
        document_html = utils.build_document(data)
        return jsonify({"success": True, "document": document_html})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
