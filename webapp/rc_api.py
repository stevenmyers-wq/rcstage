import requests
from flask import session, current_app, has_request_context
import logging

# Configure logging
logger = logging.getLogger(__name__)

# --- Refresh Token Helper ---
def refresh_rc_token():
    """Attempts to refresh the RingCentral access token using the stored refresh token."""
    refresh_token = session.get('rc_refresh_token')
    client_id = session.get('rc_current_client_id')
    
    if not refresh_token or not client_id:
        return False
        
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    token_url = f"{base_url}/restapi/oauth/token"
    
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id
    }
    
    try:
        resp = requests.post(token_url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if resp.ok:
            token_data = resp.json()
            session['rc_access_token'] = token_data.get('access_token')
            session['rc_refresh_token'] = token_data.get('refresh_token')
            session.modified = True
            logger.info("Successfully refreshed RingCentral access token.")
            return True
    except Exception as e:
        logger.error(f"Exception during token refresh: {e}")
        
    # If refresh fails (e.g., refresh token expired), clear session tokens to force re-auth
    logger.warning("Token refresh failed. User must re-authenticate.")
    session.pop('rc_access_token', None)
    session.pop('rc_refresh_token', None)
    return False

# --- Compatibility Layer ---
class MockResponse:
    """
    Minimal mock of requests.Response to prevent 'NoneType' errors
    in legacy code when API calls fail early (e.g. no token).
    """
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300
    
    def json(self):
        return {"error": self.text}

class RCWrapper:
    """
    Wraps the functional rc_api_call into an object structure 
    to satisfy imports expecting 'rc' (like in __init__.py).
    """
    def get(self, endpoint, **kwargs):
        return rc_api_call(endpoint, method='GET', return_response=True, **kwargs)

    def post(self, endpoint, **kwargs):
        return rc_api_call(endpoint, method='POST', return_response=True, **kwargs)

    def put(self, endpoint, **kwargs):
        return rc_api_call(endpoint, method='PUT', return_response=True, **kwargs)

    def delete(self, endpoint, **kwargs):
        return rc_api_call(endpoint, method='DELETE', return_response=True, **kwargs)

# Initialize the 'rc' instance immediately so it is available for imports
rc = RCWrapper()

def rc_api_call(endpoint, params=None, method='GET', raise_error=False, return_response=False, token=None, **kwargs):
    """
    Generic RingCentral API handler.
    """
    access_token = token
    
    # Try to get token from session if not provided and context is available
    if not access_token:
        if has_request_context():
            access_token = session.get('rc_access_token')
        else:
            logger.debug("No request context. Skipping session token check.")

    if not access_token:
        error_msg = "Error: No access token found (session missing or empty)."
        print(error_msg)
        if raise_error:
            raise Exception("No access token found. Please login again or pass token explicitly.")
        
        if return_response:
            return MockResponse(401, error_msg)
            
        return None

    base_url = 'https://platform.ringcentral.com'
    if current_app:
        base_url = current_app.config.get('RC_SERVER_URL', base_url)
    
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    url = f"{base_url}{endpoint}"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    if 'files' not in kwargs:
        headers['Content-Type'] = 'application/json'

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            **kwargs 
        )
        
        # --- 401 INTERCEPTION AND RETRY BLOCK ---
        if response.status_code == 401 and has_request_context() and not token:
            logger.info("Received 401 Unauthorized. Attempting to refresh token...")
            if refresh_rc_token():
                # Token refreshed successfully, update headers and retry the exact same request
                headers['Authorization'] = f"Bearer {session.get('rc_access_token')}"
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    **kwargs 
                )
        # ----------------------------------------
        
        if return_response:
            return response
        
        if response.status_code == 204:
            return {"success": True}

        if raise_error:
            response.raise_for_status()

        if not raise_error:
            response.raise_for_status()
            
        return response.json()

    except Exception as e:
        rc_error_text = "No additional RC error body provided."
        
        # Pull the raw RingCentral JSON rejection out of the request exception
        if hasattr(e, 'response') and e.response is not None:
            rc_error_text = e.response.text
        elif 'response' in locals() and response is not None:
            rc_error_text = response.text
            
        # Violently log this to GCP so we never fly blind again
        logger.error(f"RC API Error [{method} {endpoint}]: {e}")
        logger.error(f"RAW RINGCENTRAL ERROR BODY: {rc_error_text}")
        print(f"RAW RINGCENTRAL ERROR BODY: {rc_error_text}")
        
        if raise_error:
            # Append the RC payload to the Python exception so it bubbles up to your UI
            raise Exception(f"RingCentral API Error: {rc_error_text}")
            
        if return_response:
            return MockResponse(getattr(response, 'status_code', 500), rc_error_text)
            
        return None