import requests
from flask import session, current_app, has_request_context
import logging

# Configure logging
logger = logging.getLogger(__name__)

# --- Compatibility Layer ---
# Defined at the top to prevent circular import issues where 'rc' 
# is needed before the module fully initializes.

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
    
    NOTE: We pass return_response=True to ensure legacy code gets a 
    Response object (with .status_code) rather than a JSON dict.
    """
    def get(self, endpoint, **kwargs):
        # Late binding ensures rc_api_call is found even if defined below
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
    - raise_error=True: Raises exception on failure (useful for debugging specific errors).
    - raise_error=False: Returns None on failure (safer for general UI loading).
    - return_response=True: Returns the raw requests.Response object instead of JSON.
    - token: Optional manual token override (essential for background tasks without session).
    """
    access_token = token
    
    # Try to get token from session if not provided and context is available
    if not access_token:
        if has_request_context():
            access_token = session.get('rc_access_token')
        else:
            # We are in a background task/thread without a user session
            logger.debug("No request context. Skipping session token check.")

    if not access_token:
        error_msg = "Error: No access token found (session missing or empty)."
        print(error_msg)
        if raise_error:
            raise Exception("No access token found. Please login again or pass token explicitly.")
        
        # If legacy code expects a Response object, return a 401 MockResponse instead of None
        if return_response:
            return MockResponse(401, error_msg)
            
        return None

    # Get Base URL from config
    # Note: current_app requires app context. If running purely standalone, this might also need a fallback.
    base_url = 'https://platform.ringcentral.com'
    if current_app:
        base_url = current_app.config.get('RC_SERVER_URL', base_url)
    
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    url = f"{base_url}{endpoint}"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            **kwargs 
        )
        
        # If caller wants the raw response (e.g. to check status_code manually)
        if return_response:
            return response
        
        # Handle 204 No Content (Success)
        if response.status_code == 204:
            return {"success": True}

        # If we want to catch specific errors in the route, raise them here
        if raise_error:
            response.raise_for_status()

        # For normal mode, only raise if NOT raise_error (handled by except block below)
        if not raise_error:
            response.raise_for_status()
            
        return response.json()

    except Exception as e:
        if raise_error:
            # Re-raise the exception so routes.py can catch it and show the message
            raise e
            
        print(f"RC API Error [{method} {endpoint}]: {e}")
        if 'response' in locals() and response is not None:
            # For debug prints only
            # print(f"Details: {response.text}")
            pass
        
        # If legacy code expects a Response object, return a 500 MockResponse instead of None
        if return_response:
            return MockResponse(500, str(e))
            
        return None
