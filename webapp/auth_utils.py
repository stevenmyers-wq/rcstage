import secrets
import base64
import hashlib
import requests
from functools import wraps
from flask import session, jsonify

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
