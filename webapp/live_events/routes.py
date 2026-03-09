# webapp/live_events/routes.py
from flask import Blueprint, jsonify
from webapp.auth_utils import require_rc_token
from . import utils

live_events_bp = Blueprint(
    'live_events_bp', __name__,
    url_prefix='/api/live_events'
)

@live_events_bp.route('/wss-credentials', methods=['POST'])
@require_rc_token
def get_wss_token():
    """Fetches the credentials needed to establish a direct WebSocket connection."""
    try:
        wss_credentials = utils.get_wss_credentials()
        if not wss_credentials or 'uri' not in wss_credentials:
             raise Exception(f"Failed to get WSS credentials. API returned: {wss_credentials}")
        return jsonify(wss_credentials)
    except Exception as e:
        print(f"Error getting WSS credentials: {e}")
        return jsonify({"error": "An internal error occurred during WSS token creation."}), 500
