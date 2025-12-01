import requests
from flask import session, current_app

def rc_api_call(endpoint, params=None, method='GET', **kwargs):
    """
    Generic RingCentral API handler.
    Automatically adds Authorization header from session.
    Supports GET, POST, PUT, DELETE via the 'method' argument.
    Pass JSON data using the 'json' argument.
    """
    access_token = session.get('rc_access_token')
    if not access_token:
        print("Error: No access token in session.")
        return None

    # Get Base URL from config (default to Prod if missing)
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    
    # Ensure endpoint format
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    url = f"{base_url}{endpoint}"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    try:
        # We pass **kwargs to allow arguments like 'json' or 'data' to flow through to requests
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            **kwargs 
        )
        
        # Handle 204 No Content (Common in updates)
        if response.status_code == 204:
            return {"success": True}

        # Raise error for 4xx/5xx to be caught by the except block
        response.raise_for_status()
            
        return response.json()

    except Exception as e:
        print(f"RC API Error [{method} {endpoint}]: {e}")
        # If response exists, print the detailed error from RingCentral
        if 'response' in locals() and response is not None:
            print(f"Details: {response.text}")
        return None
