# webapp/live_events/utils.py
from webapp.rc_api import rc_api_call

def get_wss_credentials():
    """
    Calls the /wstoken endpoint to get a temporary, single-use
    WebSocket access token and the server URI.
    """
    try:
        response = rc_api_call("/restapi/oauth/wstoken", method="POST", json={})
        return response
    except Exception as e:
        print(f"FATAL ERROR in get_wss_credentials: {e}")
        raise e
