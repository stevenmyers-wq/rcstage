import time
from webapp.rc_api import rc_api_call

def fetch_all_extensions(token):
    """Fetches all extensions for the audit."""
    extensions = []
    page = 1
    while True:
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension?perPage=1000&page={page}", token=token, raise_error=False)
        if not resp or 'records' not in resp: 
            break
        extensions.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return extensions

def update_extension_number(ext_id, new_number, token):
    """Updates the extension number for a given extension ID."""
    endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}"
    payload = {
        "extensionNumber": str(new_number)
    }
    
    # We use a retry wrapper to gracefully handle 429 rate limits
    for attempt in range(4):
        resp = rc_api_call(endpoint, method="PUT", json=payload, token=token, return_response=True)
        status_code = getattr(resp, 'status_code', None)
        
        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 10
            time.sleep(retry_after + 1)
            continue
            
        if resp and getattr(resp, 'ok', False):
            return True, "Success"
            
        try:
            err_data = resp.json()
            err_msg = err_data.get('message', str(err_data))
        except:
            err_msg = f"HTTP {status_code}"
            
        return False, err_msg
        
    return False, "Rate limit exceeded"
