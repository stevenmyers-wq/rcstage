import requests
from flask import session, current_app

def rc_api_call(endpoint, params=None, method='GET', raise_error=False, **kwargs):
    """
    Generic RingCentral API handler.
    - raise_error=True: Raises exception on failure (useful for debugging specific errors).
    - raise_error=False: Returns None on failure (safer for general UI loading).
    """
    access_token = session.get('rc_access_token')
    if not access_token:
        print("Error: No access token in session.")
        if raise_error:
            raise Exception("No access token found. Please login again.")
        return None

    # Get Base URL from config
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    
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
            print(f"Details: {response.text}")
        return None
