# webapp/live_events/routes.py
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from . import utils

live_events_bp = Blueprint(
    'live_events_bp', __name__,
    url_prefix='/api/live_events'
)

@live_events_bp.route('/subscriptions', methods=['GET'])
@require_rc_token
def get_subscriptions_list():
    """Fetches the current list of all active WebHook subscriptions."""
    try:
        subscriptions_data = utils.list_subscriptions()
        return jsonify(subscriptions_data)
    except Exception as e:
        print(f"Error fetching subscriptions list: {e}")
        return jsonify({"error": "An internal error occurred while fetching subscriptions."}), 500

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

@live_events_bp.route('/subscriptions/<subscription_id>', methods=['DELETE'])
@require_rc_token
def remove_subscription(subscription_id):
    """Deletes a specific WebHook subscription by its ID."""
    if not subscription_id:
        return jsonify({"error": "Subscription ID is required."}), 400
        
    try:
        utils.delete_subscription(subscription_id)
        return jsonify({"status": "success", "message": f"Subscription {subscription_id} deleted."})
    except Exception as e:
        print(f"Error deleting subscription {subscription_id}: {e}")
        return jsonify({"error": f"Failed to delete subscription {subscription_id}."}), 500
