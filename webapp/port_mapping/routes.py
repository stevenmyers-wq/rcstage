import os
import re
import traceback
import base64
import hashlib
import secrets
import requests
from urllib.parse import urlencode
from flask import Blueprint, jsonify, request, send_file, session, redirect, current_app, url_for
from webapp.usage_tracking import track_usage
from . import utils

port_mapping_bp = Blueprint('port_mapping_bp', __name__, url_prefix='/api/port_mapping')

def create_pkce_challenge():
    """Generates PKCE code verifier and challenge for the isolated OAuth flow."""
    code_verifier = secrets.token_urlsafe(96)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode('ascii')
    return code_verifier, code_challenge

def get_strict_redirect_uri():
    """Fetches a strict redirect URI to prevent 'Redirect URIs do not match' OAuth errors."""
    # Defaults to localhost if not set in .env
    return os.getenv('PM_REDIRECT_URI', 'https://rcau-api-tools-396158962307.us-central1.run.app/api/port_mapping/oauth2callback')

@port_mapping_bp.route('/auth', methods=['GET'])
def pm_auth():
    """Initiates the OAuth flow specifically for Port Mapping using the DEMO App Credentials."""
    code_verifier, code_challenge = create_pkce_challenge()
    session['pm_code_verifier'] = code_verifier
    
    redirect_uri = get_strict_redirect_uri()
    client_id = os.getenv('DEMO_RC_CLIENT_ID')
    
    if not client_id:
        return "DEMO_RC_CLIENT_ID not found in environment variables.", 500
    
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'state': 'portmapping'
    }
    
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    auth_url = f"{base_url}/restapi/oauth/authorize?{urlencode(params)}"
    return redirect(auth_url)

@port_mapping_bp.route('/oauth2callback', methods=['GET'])
def pm_oauth2callback():
    """Handles the callback for Port Mapping's isolated OAuth flow."""
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
        
    redirect_uri = get_strict_redirect_uri()
    code_verifier = session.pop('pm_code_verifier', None)
    
    client_id = os.getenv('DEMO_RC_CLIENT_ID')
    client_secret = os.getenv('DEMO_RC_CLIENT_SECRET')
    
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }
    
    if code_verifier:
        data['code_verifier'] = code_verifier
        
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    token_url = f"{base_url}/restapi/oauth/token"
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    
    if client_secret:
        auth_str = f"{client_id}:{client_secret}"
        headers['Authorization'] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
    else:
        data['client_id'] = client_id
    
    response = requests.post(token_url, data=data, headers=headers)
    if response.ok:
        token_data = response.json()
        session['pm_employee_token'] = token_data.get('access_token')
        return redirect("/?tab=port_mapping#port-mapping")
    else:
        print(f"Token Exchange Failed! Sent Redirect URI: {redirect_uri}")
        return jsonify({"error": "Failed to exchange code", "details": response.json()}), 400

@port_mapping_bp.route('/bridge', methods=['POST'])
def create_bridge():
    """Instantly swaps the employee token for a customer-scoped token using the PS Auth bridge."""
    data = request.json
    target_id = data.get('targetAccountId')
    
    if not target_id:
        return jsonify({"error": "Target Account ID is required"}), 400
        
    # Strictly use the isolated Port Mapping employee token
    employee_token = session.get('pm_employee_token')
    if not employee_token:
        return jsonify({"error": "Not authenticated. Please click 'Sign In (Port Mapping)' first."}), 401
        
    # Bridge swap via HAR file's exact PS endpoint logic
    customer_token = utils.get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['pm_isolated_token'] = customer_token
        session['pm_target_id'] = target_id
        return jsonify({"success": True})
    
    return jsonify({"error": "Impersonation Bridge Failed. Ensure you are logged in and the target ID is valid."}), 403

@port_mapping_bp.route('/logout')
def port_mapping_logout():
    """Clears the customer-scoped token and Port Mapping employee token."""
    session.pop('pm_isolated_token', None)
    session.pop('pm_target_id', None)
    session.pop('pm_employee_token', None)
    return redirect("/?tab=port_mapping#port-mapping")

@port_mapping_bp.route('/process', methods=['POST'])
@track_usage('Port Mapping (Bridged)')
def process_mapping():
    token = session.get('pm_isolated_token')
    if not token:
        return jsonify({"error": "Unauthorized: Please Bridge the connection first."}), 401

    loa_file = request.files.get('loa_file')
    loa_url = request.form.get('loa_url')
    brd_file = request.files.get('brd_file')
    brd_url = request.form.get('brd_url')

    if not loa_file and not loa_url: return jsonify({"error": "LOA (PDF) file or URL is required."}), 400
    if not brd_file and not brd_url: return jsonify({"error": "BRD (Excel) file or URL is required."}), 400

    loa_bytes = loa_file_id = brd_bytes = brd_file_id = None

    if loa_file:
        if not loa_file.filename.lower().endswith('.pdf'): return jsonify({"error": "LOA must be a PDF file."}), 400
        loa_bytes = loa_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", loa_url)
        if not match: return jsonify({"error": "Invalid LOA Google Drive URL."}), 400
        loa_file_id = match.group(1)

    if brd_file:
        if not (brd_file.filename.lower().endswith('.xlsx') or brd_file.filename.lower().endswith('.xls')):
            return jsonify({"error": "BRD must be an Excel file."}), 400
        brd_bytes = brd_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", brd_url)
        if not match: return jsonify({"error": "Invalid BRD Google Drive URL."}), 400
        brd_file_id = match.group(1)

    try:
        output_buffer = utils.process_port_mapping(
            token=token, 
            loa_bytes=loa_bytes, 
            loa_file_id=loa_file_id, 
            brd_bytes=brd_bytes, 
            brd_file_id=brd_file_id
        )
        return send_file(
            output_buffer,
            download_name="Processed_Port_Mapping.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
