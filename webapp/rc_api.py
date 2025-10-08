# webapp/rc_api.py
import time
import requests
from flask import session, current_app
from webapp.auth_utils import get_rc_access_token

def rc_api_call(endpoint, method="GET", body=None, params=None) -> dict | None:
    """Makes a generic, authenticated call to the RingCentral API with session logging."""
    rc_token = get_rc_access_token()
    
    if 'api_log' not in session:
        session['api_log'] = []

    if not rc_token:
        session['api_log'].append({'status': 'FAIL', 'endpoint': endpoint, 'detail': 'Token missing'})
        session.modified = True
        return None

    url = f"{current_app.config['RC_SERVER_URL']}{endpoint}"
    headers = {"Authorization": f"Bearer {rc_token}", "Accept": "application/json"}
    start_time = time.time()
    
    try:
        response = requests.request(method.upper(), url, headers=headers, params=params, json=body)
        # Gracefully handle 404 Not Found as a valid "empty" response
        if response.status_code == 404:
            return None
        
        response.raise_for_status()
        duration = (time.time() - start_time) * 1000
        session['api_log'].append({'status': 'SUCCESS', 'endpoint': endpoint, 'code': response.status_code, 'duration': f"{duration:.0f}ms", 'method': method})
        session.modified = True
        return response.json() if response.content else {"status": "success", "content_empty": True}
    except requests.exceptions.RequestException as e:
        duration = (time.time() - start_time) * 1000
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text if e.response is not None else 'No response body'
        # Don't log expected 404s as failures
        if status_code != 404:
            session['api_log'].append({'status': 'FAIL', 'endpoint': endpoint, 'code': status_code, 'duration': f"{duration:.0f}ms", 'method': method, 'detail': response_text[:100]})
            session.modified = True
        return None