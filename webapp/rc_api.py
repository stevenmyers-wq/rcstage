import time
import requests
from flask import session, current_app, has_request_context
import logging

logger = logging.getLogger(__name__)

# RingCentral throttles with 429 (rate limit) and occasionally 503 (service
# temporarily unavailable). Both are transient: the correct response is to wait
# and retry, not to drop the call. Honour the Retry-After header when present,
# otherwise fall back to exponential backoff.
RATE_LIMIT_STATUSES = (429, 503)
MAX_RATE_LIMIT_RETRIES = 5
MAX_RATE_LIMIT_SLEEP = 60  # seconds; safety cap per wait


def _retry_after_seconds(response, attempt):
    """How long to wait before retrying a throttled request.

    RingCentral returns a Retry-After header (in seconds) on 429; respect it so
    we sleep exactly until the rate-limit window reopens. When it's missing or
    unparseable, back off exponentially (1, 2, 4, 8 ...) capped to a sane max.
    """
    retry_after = None
    try:
        retry_after = response.headers.get('Retry-After')
    except Exception:
        retry_after = None
    if retry_after:
        try:
            return max(1, min(int(float(retry_after)), MAX_RATE_LIMIT_SLEEP))
        except (TypeError, ValueError):
            pass
    return min(2 ** attempt, MAX_RATE_LIMIT_SLEEP)


def _rewind_files(kwargs):
    """Best-effort rewind of any seekable file-like objects in a multipart
    upload so a retried request re-sends the full body. In-memory bytes/str
    payloads (the common case here) need nothing; this only matters if a caller
    ever passes an open file handle."""
    files = kwargs.get('files')
    if not files:
        return
    entries = files.values() if isinstance(files, dict) else files
    for entry in entries:
        obj = entry
        if isinstance(entry, (tuple, list)):
            obj = entry[1] if len(entry) > 1 else None
        try:
            if hasattr(obj, 'seek'):
                obj.seek(0)
        except Exception:
            pass

def refresh_rc_token():
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
        
    logger.warning("Token refresh failed. User must re-authenticate.")
    session.pop('rc_access_token', None)
    session.pop('rc_refresh_token', None)
    return False

class MockResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300
    def json(self):
        return {"error": self.text}

class RCWrapper:
    def get(self, endpoint, **kwargs): return rc_api_call(endpoint, method='GET', return_response=True, **kwargs)
    def post(self, endpoint, **kwargs): return rc_api_call(endpoint, method='POST', return_response=True, **kwargs)
    def put(self, endpoint, **kwargs): return rc_api_call(endpoint, method='PUT', return_response=True, **kwargs)
    def delete(self, endpoint, **kwargs): return rc_api_call(endpoint, method='DELETE', return_response=True, **kwargs)

rc = RCWrapper()

def rc_api_call(endpoint, params=None, method='GET', raise_error=False, return_response=False, token=None, **kwargs):
    access_token = token
    
    if not access_token:
        if has_request_context():
            # Prefer SM impersonation token over standard PKCE token for all UC tools
            access_token = session.get('sm_isolated_token') or session.get('rc_access_token')
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
    
    if not endpoint.startswith('/'): endpoint = '/' + endpoint
    url = f"{base_url}{endpoint}"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    if 'files' not in kwargs:
        headers['Content-Type'] = 'application/json'

    try:
        response = None
        did_token_refresh = False
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = requests.request(method=method, url=url, headers=headers, params=params, **kwargs)

            if (response.status_code == 401 and has_request_context()
                    and not token and not did_token_refresh):
                did_token_refresh = True
                logger.info("Received 401 Unauthorized. Attempting to refresh token...")
                new_auth = None
                if session.get('sm_isolated_token'):
                    # Impersonation path: silently re-mint the customer bridge token
                    # (refreshing the employee token first if needed) so long-running
                    # work doesn't force the user to rebuild the bridge by hand.
                    from webapp.auth_utils import refresh_sm_isolated_token
                    if refresh_sm_isolated_token():
                        new_auth = session.get('sm_isolated_token')
                elif refresh_rc_token():
                    new_auth = session.get('rc_access_token')

                if new_auth:
                    headers['Authorization'] = f"Bearer {new_auth}"
                    response = requests.request(method=method, url=url, headers=headers, params=params, **kwargs)

            # Rate limited / temporarily unavailable → wait for the window to
            # reopen and retry, rather than dropping the call.
            if (response.status_code in RATE_LIMIT_STATUSES
                    and attempt < MAX_RATE_LIMIT_RETRIES):
                wait = _retry_after_seconds(response, attempt)
                logger.warning(
                    f"RingCentral throttled [{response.status_code}] {method} {endpoint}; "
                    f"waiting {wait}s before retry {attempt + 1}/{MAX_RATE_LIMIT_RETRIES}"
                )
                _rewind_files(kwargs)
                time.sleep(wait)
                continue

            break

        if return_response: return response
        if response.status_code == 204: return {"success": True}
        if raise_error: response.raise_for_status()
        return response.json()

    except Exception as e:
        rc_error_text = "No additional RC error body provided."
        if hasattr(e, 'response') and e.response is not None:
            rc_error_text = e.response.text
        elif 'response' in locals() and response is not None:
            rc_error_text = response.text
            
        logger.error(f"RC API Error [{method} {endpoint}]: {e}")
        logger.error(f"RAW RINGCENTRAL ERROR BODY: {rc_error_text}")
        
        if raise_error: raise Exception(f"RingCentral API Error: {rc_error_text}")
        if return_response: return MockResponse(getattr(response, 'status_code', 500), rc_error_text)
        return None
