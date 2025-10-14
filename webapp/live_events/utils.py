# webapp/live_events/utils.py
from webapp.rc_api import rc_api_call

def list_subscriptions():
    """
    Retrieves a list of all active WebHook subscriptions for the account.
    This is useful for cleaning up old, non-WebSocket subscriptions.
    """
    try:
        response = rc_api_call("/restapi/v1.0/subscription")
        return response.get('records', []) if response else []
    except Exception as e:
        print(f"FATAL ERROR in list_subscriptions: {e}")
        raise e

def get_wss_credentials():
    """
    Calls the /wstoken endpoint to get a temporary, single-use
    WebSocket access token and the server URI.
    """
    try:
        response = rc_api_call("/restapi/oauth/wstoken", method="POST")
        return response
    except Exception as e:
        print(f"FATAL ERROR in get_wss_credentials: {e}")
        raise e

def delete_subscription(subscription_id):
    """
    Deletes a WebHook subscription by its ID.
    """
    try:
        rc_api_call(f"/restapi/v1.0/subscription/{subscription_id}", method="DELETE")
        return True
    except Exception as e:
        print(f"FATAL ERROR in delete_subscription: {e}")
        raise e
