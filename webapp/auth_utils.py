# webapp/auth_utils.py
import secrets
import base64
import hashlib
from functools import wraps
from flask import session, jsonify

def is_authenticated() -> bool:
    """Checks for a successful website login session (Layer 1)."""
    return session.get('authenticated', False) and session.get('user_email') is not None

def is_admin_user() -> bool:
    """Checks if the currently logged-in website user has admin privileges."""
    return session.get('is_admin', False)

def get_rc_access_token() -> str | None:
    """Retrieves the user's dynamic RingCentral token from the session (Layer 2)."""
    return session.get('rc_access_token')

def create_pkce_challenge():
    """Standard, robust PKCE challenge generation."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(hashed).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge

# --- ADD THIS NEW FUNCTION ---
def require_rc_token(f):
    """
    A decorator to protect API endpoints.
    
    It checks if a RingCentral access token exists in the user's session.
    If not, it returns a 401 Unauthorized error as a JSON response.
    This is the cleaner, reusable version of the manual 'if' check.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'rc_access_token' not in session:
            return jsonify({
                "error": "RingCentral authentication required.",
                "message": "Please connect to a RingCentral account first."
            }), 401
        return f(*args, **kwargs)
    return decorated_function
