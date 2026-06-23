import os
import secrets
import requests
import base64
import hashlib
from urllib.parse import urlencode
from flask import (
    Blueprint, render_template, request, session, jsonify, redirect, url_for,
    current_app
)
from webapp.auth_utils import is_authenticated, get_rc_access_token, create_pkce_challenge, get_impersonation_token

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/auth/initiate-pkce', methods=['POST'])
def initiate_pkce():
    if not is_authenticated():
        return jsonify({'status': 'error', 'message': 'Not unlocked.'}), 401
    
    data = request.get_json()
    client_id = data.get('client_id') if data else None

    if not client_id:
        return jsonify({'status': 'error', 'message': 'Client ID is required.'}), 400

    code_verifier, code_challenge = create_pkce_challenge()
    
    session['rc_client_id'] = client_id
    session['rc_code_verifier'] = code_verifier
    session['rc_state'] = secrets.token_urlsafe(16)
    
    redirect_uri = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback")
    scope_value = os.getenv("RC_SCOPE", "ReadAccounts ReadCallLog")
    
    params = {
        'response_type': 'code', 
        'client_id': client_id, 
        'redirect_uri': redirect_uri,
        'code_challenge': code_challenge, 
        'code_challenge_method': 'S256',
        'scope': scope_value, 
        'state': session['rc_state']
    }
    
    auth_url = 'https://platform.ringcentral.com/restapi/oauth/authorize?' + urlencode(params)
    return jsonify({'status': 'success', 'redirect_url': auth_url}), 200

@auth_bp.route('/auth/callback', methods=['GET'])
def auth_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error:
        return render_template('error.html', message=f"Auth Error: {error}"), 400
    if state != session.get('rc_state'):
        return render_template('error.html', message="State mismatch."), 403

    client_id = session.pop('rc_client_id', None)
    code_verifier = session.pop('rc_code_verifier', None)
    session.pop('rc_state', None)

    if not all([code, client_id, code_verifier]):
        return render_template('error.html', message="PKCE flow failed: Missing session context."), 400

    redirect_uri = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback")
    token_url = f"{current_app.config['RC_SERVER_URL']}/restapi/oauth/token"
    
    data = {
        'grant_type': 'authorization_code', 
        'code': code, 
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier, 
        'client_id': client_id
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    try:
        response = requests.post(token_url, data=data, headers=headers) 
        response.raise_for_status() 
        token_data = response.json()
        
        session['rc_access_token'] = token_data.get('access_token')
        session['rc_refresh_token'] = token_data.get('refresh_token') 
        session['rc_current_client_id'] = client_id
        session['rc_user_email'] = token_data.get('owner_id')
        
        return redirect(url_for('core.index', tab='auth_rex'))
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response else 'N/A'
        response_text = e.response.text if e.response else 'No body.'
        error_details = e.response.json() if e.response and 'application/json' in e.response.headers.get('Content-Type', '') else {}
        error_message = error_details.get('error_description', response_text)
        full_message = f"Token exchange failed. Status: {status_code}. Detail: {error_message}"
        return render_template('error.html', message=full_message), 500

@auth_bp.route('/api/rc/disconnect', methods=['POST'])
def rc_disconnect():
    session.pop('rc_access_token', None)
    session.pop('rc_refresh_token', None) 
    session.pop('rc_current_client_id', None)
    session.pop('rc_user_email', None)
    return jsonify({'status': 'success', 'message': 'Disconnected.'}), 200

@auth_bp.route('/api/rc/status')
def get_rc_status():
    token = get_rc_access_token()
    return jsonify({
        'status': 'connected' if token else 'disconnected',
        'client_id': session.get('rc_current_client_id'),
        'rc_user_email': session.get('rc_user_email')
    }), 200

# =====================================================================
# CENTRALIZED SM IMPERSONATION AUTH FLOW
# =====================================================================

@auth_bp.route('/api/sm_auth/login', methods=['GET'])
def sm_auth_login():
    target_tab = request.args.get('tab', 'index')
    code_verifier, code_challenge = create_pkce_challenge()
    session['sm_code_verifier'] = code_verifier
    
    # 1. Generate the URI and forcefully strip any proxy ports Cloud Run might append
    redirect_uri = url_for('auth.sm_oauth2callback', _external=True, _scheme='https').replace(':443', '')
    
    # 2. SAVE the exact string to the session to guarantee parity in step 2
    session['sm_redirect_uri'] = redirect_uri
    
    client_id = os.getenv('SM_CLIENT_ID')
    
    if not client_id:
        return "SM_CLIENT_ID not found in environment variables.", 500
        
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'state': target_tab 
    }
    
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    return redirect(f"{base_url}/restapi/oauth/authorize?{urlencode(params)}")

@auth_bp.route('/api/sm_auth/callback', methods=['GET'])
def sm_oauth2callback():
    code = request.args.get('code')
    target_tab = request.args.get('state', 'index') 
    if not code: return "No code provided", 400
        
    # 3. Retrieve the EXACT string used in Step 1
    redirect_uri = session.get('sm_redirect_uri')
    if not redirect_uri:
        # Failsafe fallback
        redirect_uri = url_for('auth.sm_oauth2callback', _external=True, _scheme='https').replace(':443', '')
        
    code_verifier = session.pop('sm_code_verifier', None)
    
    client_id = os.getenv('SM_CLIENT_ID')
    client_secret = os.getenv('SM_CLIENT_SECRET')
    
    data = { 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri }
    if code_verifier: data['code_verifier'] = code_verifier
        
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    headers = { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' }
    
    if client_secret:
        auth_str = f"{client_id}:{client_secret}"
        headers['Authorization'] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
    else:
        data['client_id'] = client_id
        
    response = requests.post(f"{base_url}/restapi/oauth/token", data=data, headers=headers)
    
    if response.ok:
        session['sm_employee_token'] = response.json().get('access_token')
        session.pop('sm_redirect_uri', None) # Clean up
        return redirect(f"/?tab={target_tab}")
        
    return jsonify({"error": "Failed to exchange code", "details": response.json()}), 400

@auth_bp.route('/api/sm_auth/bridge', methods=['POST'])
def sm_create_bridge():
    target_id = request.json.get('targetAccountId')
    employee_token = session.get('sm_employee_token')
    
    if not target_id: return jsonify({"error": "Target Account ID is required"}), 400
    if not employee_token: return jsonify({"error": "Not authenticated. Please Sign In first."}), 401
    
    customer_token = get_impersonation_token(employee_token, target_id)
    if customer_token:
        session['sm_isolated_token'] = customer_token
        session['sm_target_id'] = target_id
        return jsonify({"success": True})
        
    return jsonify({"error": "Impersonation Bridge Failed. Ensure you are logged in and the target ID is valid."}), 403

@auth_bp.route('/api/sm_auth/logout')
def sm_logout():
    """Drops the bridge connection but keeps the user logged in as an employee."""
    target_tab = request.args.get('tab', 'index')
    session.pop('sm_isolated_token', None)
    session.pop('sm_target_id', None)
    return redirect(f"/?tab={target_tab}")

@auth_bp.route('/api/sm_auth/full_logout')
def sm_full_logout():
    """Drops the bridge AND logs the user completely out of the SM Auth app."""
    target_tab = request.args.get('tab', 'index')
    session.pop('sm_isolated_token', None)
    session.pop('sm_target_id', None)
    session.pop('sm_employee_token', None)
    return redirect(f"/?tab={target_tab}")
