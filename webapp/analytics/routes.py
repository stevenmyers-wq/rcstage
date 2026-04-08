import os
import requests
import base64
import logging
from flask import Blueprint, request, session, redirect, url_for, jsonify
from webapp.analytics.utils import RCImpersonator

analytics_bp = Blueprint('analytics', __name__)

# Pulling from environment variables as requested
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

@analytics_bp.route('/api/auth/start-impersonation')
def start_impersonation():
    """
    Step 1: Save the target account ID and redirect the user 
    to authenticate their own account (The Impersonator).
    """
    target_id = request.args.get('target_id')
    if not target_id:
        return "Target Account ID is required", 400
    
    session['temp_target_id'] = target_id
    
    # Standard OAuth Authorize URL using your source credentials
    redirect_uri = url_for('analytics.impersonation_callback', _external=True)
    auth_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize?"
        f"response_type=code&client_id={CLIENT_ID}&redirect_uri={redirect_uri}"
    )
    return redirect(auth_url)

@analytics_bp.route('/api/auth/impersonation-callback')
def impersonation_callback():
    """
    Step 2: User returns from login. We use their token to find the 
    target admin and swap identities via the Interop API.
    """
    code = request.args.get('code')
    target_id = session.get('temp_target_id')
    
    if not code or not target_id:
        return "Authorization failed: Missing code or target session ID", 400
    
    try:
        # 1. Exchange code for YOUR token (The Impersonator)
        source_token_data = exchange_code_for_token(code)
        source_token = source_token_data.get('access_token')
        
        # 2. Identify the Super Admin of the target account
        imp = RCImpersonator(target_id)
        admin_id = imp.get_super_admin_id(source_token)
        
        # 3. Generate Impersonation Code for THIS APP (CLIENT_ID)
        code_res = imp.generate_impersonation_code(admin_id, source_token, CLIENT_ID)
        
        if 'code' in code_res:
            # 4. Exchange the impersonation code for the Final Admin Token
            # Note: The 'redirectUri' for this specific step is provided by the Interop API
            final_token_data = exchange_code_for_token(
                code_res['code'], 
                redirect_uri=code_res.get('redirectUri')
            )
            
            # 5. Store the final Super Admin session
            session['imp_token'] = final_token_data.get('access_token')
            session['imp_active'] = True
            session['imp_acc_id'] = target_id
            
            # Clean up temp data
            session.pop('temp_target_id', None)
            
            return redirect('/#business-analytics') 
        else:
            return jsonify({"error": "Impersonation handshake failed", "details": code_res}), 400

    except Exception as e:
        logging.error(f"Impersonation Flow Exception: {str(e)}")
        return f"Authentication Error: {str(e)}", 500

def exchange_code_for_token(code, redirect_uri=None):
    """Utility to swap an auth code for an access token."""
    # Default to our callback if no specific redirect_uri is provided by the Interop API
    red = redirect_uri if redirect_uri else url_for('analytics.impersonation_callback', _external=True)
    
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    
    resp = requests.post(
        "https://platform.ringcentral.com/restapi/v1.0/oauth/token",
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": red
        }
    )
    return resp.json()

@analytics_bp.route('/api/auth/imp-status')
def imp_status():
    """Helper for the UI to determine current state."""
    return jsonify({
        "active": session.get('imp_active', False),
        "target": session.get('imp_acc_id')
    })
