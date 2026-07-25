import os
import secrets
import base64
import hashlib
import requests
from functools import wraps
from flask import session, jsonify, current_app

def is_authenticated() -> bool:
    """Checks for a successful website login session (Layer 1)."""
    return session.get('authenticated', False) and session.get('user_email') is not None

def is_admin_user() -> bool:
    """Checks if the currently logged-in website user has admin privileges."""
    return session.get('is_admin', False)

def get_rc_access_token() -> str | None:
    """Retrieves the user's dynamic RingCentral token or SM impersonation token."""
    # This cascades the SM Auth support to account_migration, account_health, and sip_fetcher.
    return session.get('sm_isolated_token') or session.get('rc_access_token')

def create_pkce_challenge():
    """Standard, robust PKCE challenge generation."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(hashed).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge

def get_impersonation_token(employee_token, target_account_id):
    """Exchanges an employee token for a customer-scoped token using the whitelisted PS bridge."""
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    payload = {
        "accountId": str(target_account_id),
        "appName": "brd"
    }
    try:
        resp = requests.post(exchange_url, headers=headers, json=payload)
        if resp.ok:
            return resp.json().get("access_token")
        print(f"Token exchange failed: {resp.status_code} - {resp.text}")
        return None
    except Exception as e:
        print(f"Exception during token exchange: {e}")
        return None

def _rc_base_url():
    try:
        return current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    except Exception:
        return os.getenv('RC_SERVER_URL', 'https://platform.ringcentral.com')

def refresh_sm_employee_token():
    """Refresh the employee RingCentral OAuth token (the one used to mint
    impersonation bridges) using its stored refresh token. Returns True on
    success."""
    refresh_token = session.get('sm_employee_refresh_token')
    client_id = os.getenv('SM_CLIENT_ID')
    client_secret = os.getenv('SM_CLIENT_SECRET')

    if not refresh_token or not client_id:
        return False

    data = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}

    # Mirror the auth-code exchange: confidential client uses Basic auth,
    # public client passes the client_id in the body.
    if client_secret:
        auth_str = f"{client_id}:{client_secret}"
        headers['Authorization'] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
    else:
        data['client_id'] = client_id

    try:
        resp = requests.post(f"{_rc_base_url()}/restapi/oauth/token", data=data, headers=headers)
        if resp.ok:
            token_data = resp.json()
            session['sm_employee_token'] = token_data.get('access_token')
            if token_data.get('refresh_token'):
                session['sm_employee_refresh_token'] = token_data.get('refresh_token')
            session.modified = True
            return True
        print(f"SM employee token refresh failed: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Exception during SM employee token refresh: {e}")

    return False

def refresh_sm_isolated_token():
    """Re-mint the customer-scoped impersonation token without the user having to
    drop and rebuild the bridge. Tries the current employee token first; if that
    has itself expired, refreshes it and retries once. Returns True on success."""
    target_id = session.get('sm_target_id')
    if not target_id:
        return False

    employee_token = session.get('sm_employee_token')
    if employee_token:
        new_token = get_impersonation_token(employee_token, target_id)
        if new_token:
            session['sm_isolated_token'] = new_token
            session.modified = True
            return True

    # Employee token likely expired too — refresh it and try the bridge again.
    if refresh_sm_employee_token():
        new_token = get_impersonation_token(session.get('sm_employee_token'), target_id)
        if new_token:
            session['sm_isolated_token'] = new_token
            session.modified = True
            return True

    return False

def require_rc_token(f):
    """
    A decorator to protect API endpoints. Checks for EITHER a standard RC token
    or an isolated SM impersonation token.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'rc_access_token' not in session and 'sm_isolated_token' not in session:
            return jsonify({
                "error": "RingCentral authentication required.",
                "message": "Please connect to a RingCentral account first."
            }), 401
        return f(*args, **kwargs)
    return decorated_function
