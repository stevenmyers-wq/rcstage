import os
import secrets
import requests
from urllib.parse import urlencode
from flask import (
    Blueprint, render_template, request, session, jsonify, redirect, url_for,
    current_app
)
from webapp.auth_utils import is_authenticated, get_rc_access_token, create_pkce_challenge

# A Blueprint for RingCentral authentication routes
auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/auth/initiate-pkce', methods=['POST'])
def initiate_pkce():
    """Initiates the PKCE flow."""
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
    """Handles the token exchange."""
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
        session['rc_current_client_id'] = client_id
        session['rc_user_email'] = token_data.get('owner_id')
        
        return redirect(url_for('core.index', tab='authenticator'))
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response else 'N/A'
        response_text = e.response.text if e.response else 'No body.'
        error_details = e.response.json() if e.response and 'application/json' in e.response.headers.get('Content-Type', '') else {}
        error_message = error_details.get('error_description', response_text)
        full_message = f"Token exchange failed. Status: {status_code}. Detail: {error_message}"
        return render_template('error.html', message=full_message), 500

@auth_bp.route('/api/rc/disconnect', methods=['POST'])
def rc_disconnect():
    """Clears only the RC token state."""
    session.pop('rc_access_token', None)
    session.pop('rc_current_client_id', None)
    session.pop('rc_user_email', None)
    return jsonify({'status': 'success', 'message': 'Disconnected.'}), 200

@auth_bp.route('/api/rc/status')
def get_rc_status():
    """API endpoint to check current RingCentral connection status."""
    token = get_rc_access_token()
    return jsonify({
        'status': 'connected' if token else 'disconnected',
        'client_id': session.get('rc_current_client_id'),
        'rc_user_email': session.get('rc_user_email')
    }), 200
