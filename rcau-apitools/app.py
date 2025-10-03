import os
import secrets
import base64
import hashlib
import json 
import requests
from urllib.parse import urlencode 
from flask import Flask, request, render_template, redirect, url_for, session, jsonify, make_response
from google.cloud import firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from dotenv import load_dotenv
from datetime import datetime, timedelta 
import time 

# --- CRITICAL: RINGCENTRAL SERVER CONFIGURATION ---
# IMPORTANT: Change this value to 'https://platform.devtest.ringcentral.com' if using the Sandbox.
RC_SERVER_URL = os.getenv("RC_SERVER_URL", "https://platform.ringcentral.com")
# --------------------------------------------------

# --- CONFIGURATION & INITIALIZATION ---
if os.environ.get("FLASK_ENV") != "production":
    load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY")
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
# CRITICAL FIX: SAMESITE='Lax' is required for the OAuth redirect (PKCE flow)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' 
app.config['SESSION_COOKIE_NAME'] = 'app_session' 
app.config['SESSION_COOKIE_PATH'] = '/' 

# Database initialization (Crucial: requires Google Cloud permissions)
try:
    db = firestore.Client()
except Exception as e:
    print(f"WARNING: Could not initialize Firestore client: {e}. Check credentials.")

# --- CONSTANTS: Firestore Configuration ---
PASSCODE_COLLECTION_ID = 'RCAU_APITOOLS_WEBAPP_PASSCODE' 
PASSCODE_DOCUMENT_ID = 'passcode'
PASSCODE_FIELD = 'app_passcode' 
ADMIN_LIST_FIELD = 'admin_emails' 

# --- Utility Functions ---

def get_config_from_firestore() -> dict | None:
    """Retrieves the shared passcode and admin list from Firestore."""
    try:
        doc_ref: DocumentSnapshot = db.collection(PASSCODE_COLLECTION_ID).document(PASSCODE_DOCUMENT_ID).get()
        if doc_ref.exists:
            data = doc_ref.to_dict()
            passcode = data.get(PASSCODE_FIELD)
            return {'passcode': passcode, 'admin_list': data.get(ADMIN_LIST_FIELD, [])}
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to access Firestore: {e}")
        return None
    return None

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
    """CRITICAL FIX: Standard, robust PKCE challenge generation."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    # base64.urlsafe_b64encode ensures URL safety; rstrip('=') removes padding
    code_challenge = base64.urlsafe_b64encode(hashed).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge

# --- Call Flow Data Generation Logic ---

extension_cache = {} 

def rc_api_call(endpoint, method="GET", body=None, params=None) -> dict | None:
    """Makes a generic, authenticated call to the RingCentral API with session logging."""
    rc_token = get_rc_access_token()
    
    if 'api_log' not in session:
        session['api_log'] = []

    if not rc_token:
        session['api_log'].append({'status': 'FAIL', 'endpoint': endpoint, 'detail': 'Token missing'})
        session.modified = True
        return None
        
    global RC_SERVER_URL 
    url = f"{RC_SERVER_URL}{endpoint}" 
    
    headers = {
        "Authorization": f"Bearer {rc_token}",
        "Accept": "application/json"
    }
    
    start_time = time.time()
    
    try:
        response = requests.request(method.upper(), url, headers=headers, params=params, json=body)
        response.raise_for_status()
        
        duration = (time.time() - start_time) * 1000
        
        # Log successful call
        session['api_log'].append({
            'status': 'SUCCESS', 
            'endpoint': endpoint, 
            'code': response.status_code,
            'duration': f"{duration:.0f}ms",
            'method': method
        })
        session.modified = True
        
        if response.content:
            return response.json()
        elif response.status_code == 204 or response.status_code == 200:
            return {"status": "success", "content_empty": True}
        return None
        
    except requests.exceptions.RequestException as e:
        duration = (time.time() - start_time) * 1000
        
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text if e.response is not None else 'No response body'
        
        # Log failed call
        session['api_log'].append({
            'status': 'FAIL', 
            'endpoint': endpoint, 
            'code': status_code,
            'duration': f"{duration:.0f}ms",
            'method': method,
            'detail': response_text[:100]
        })
        session.modified = True
        
        return None

def get_extension_info(ext_id):
    """Helper to get full extension info with caching."""
    if ext_id in extension_cache: return extension_cache[ext_id]
    info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
    if info: extension_cache[ext_id] = info
    return info

def get_queue_members_info(ext_id):
    """Fetches and formats queue member names for display."""
    try:
        members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
        if not members_resp or not members_resp.get('records'): return "Members (0)", []
        
        member_details = []
        for member in members_resp['records']:
            info = get_extension_info(member.get('id')) 
            name = f"{info['contact'].get('firstName', '')} {info['contact'].get('lastName', '')}".strip() if info and info.get('contact') else "Unknown"
            member_details.append(f"{name} (Ext: {member.get('extensionNumber', 'N/A')})")
        return f"Queue Members ({len(member_details)})", member_details
    except Exception:
        return "Members: ERROR", []

def get_business_hours_summary(ext_id):
    """Fetches and formats opening hours as a summary string."""
    try:
        hours_response = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        schedule = hours_response.get('schedule')
        if not schedule or not schedule.get('weeklyRanges'): return "Hours: 24/7"
            
        weekly_ranges = schedule.get('weeklyRanges', {})
        days_active = [day.capitalize() for day in weekly_ranges if weekly_ranges[day]]
        
        if not days_active: return "Hours: Closed (All Week)"
             
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
             if day in weekly_ranges and weekly_ranges[day]:
                 time_from = weekly_ranges[day][0].get('from', 'N/A')
                 time_to = weekly_ranges[day][0].get('to', 'N/A')
                 return f"Hours: {', '.join(days_active)} {time_from} - {time_to}"

        return "Hours: Custom Schedule"
    except Exception: return "Hours: Runtime ERROR"

def parse_rule_details(detailed_rule):
    """Parses rule details into action, schedule, and target."""
    try:
        schedule_details, call_action, action_target = "N/A", "N/A", "N/A"
        schedule_obj = detailed_rule.get('schedule', {})
        
        # FIX: Prioritize date ranges over weekly schedules for more specific descriptions
        if 'ranges' in schedule_obj and schedule_obj.get('ranges'):
            first_range = schedule_obj['ranges'][0]
            date_from = datetime.fromisoformat(first_range.get('from').replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
            date_to = datetime.fromisoformat(first_range.get('to').replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
            schedule_details = f"Date Range: {date_from} to {date_to}"
        elif 'weeklyRanges' in schedule_obj and any(schedule_obj['weeklyRanges'].values()):
            schedule_details = "Custom weekly schedule"
        elif 'ref' in schedule_obj:
            schedule_details = detailed_rule.get('name') or schedule_obj['ref']

        call_action = detailed_rule.get('callHandlingAction', 'N/A')
        
        # FIX: More robust parsing for different actions and targets
        if call_action == 'TakeMessagesOnly':
            recipient = detailed_rule.get('voicemail', {}).get('recipient', {})
            action_target = f"Voicemail Box ID {recipient.get('id', 'N/A')}"
            call_action = "Voicemail"
        elif call_action in ['TransferToExtension', 'ForwardCalls']:
            transfer_ext_id = detailed_rule.get('transfer', {}).get('extension', {}).get('id')
            if transfer_ext_id:
                transfer_ext_info = get_extension_info(transfer_ext_id)
                action_target = f"Ext: {transfer_ext_info.get('extensionNumber', 'N/A')}" if transfer_ext_info else f"ID: {transfer_ext_id}"
                call_action = "TransferToExtension"
            elif 'forwarding' in detailed_rule:
                fwd_rules = detailed_rule.get('forwarding', {}).get('rules', [])
                if fwd_rules and fwd_rules[0].get('forwardingNumbers'):
                    target = fwd_rules[0]['forwardingNumbers'][0]
                    if 'phoneNumber' in target:
                        action_target = target['phoneNumber']
                        call_action = "UnconditionalForwarding"
                    elif 'extension' in target:
                        target_ext_info = get_extension_info(target['extension'].get('id'))
                        action_target = f"Ext: {target_ext_info.get('extensionNumber', 'N/A')}" if target_ext_info else "Unknown Ext"
                        call_action = "TransferToExtension"
        elif call_action == 'PlayAnnouncementOnly':
             action_target = "N/A" # This action has no target
        
        return schedule_details, call_action, action_target
    except Exception:
        return "Rule Details: ERROR", "Action: ERROR", "Target: ERROR"

def trace_flow_recursive(ext_id, node_counter, flow_data, processed_extensions):
    """Recursively traces the call flow and generates data blocks for the renderer."""
    if ext_id in processed_extensions or node_counter > 20:
        return node_counter, flow_data
        
    ext_info = get_extension_info(ext_id)
    if not ext_info:
        flow_data.append({'type': 'error', 'name': f'Error fetching Ext {ext_id}', 'details': []})
        return node_counter + 1, flow_data

    processed_extensions[ext_id] = ext_id 
    ext_type = ext_info.get('type', 'Unknown')
    ext_name = ext_info.get('name', f'ID {ext_id}')
    ext_number = ext_info.get('extensionNumber', 'N/A')
    
    # --- Custom Rules Processing ---
    rules_endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
    rules_summary = rc_api_call(rules_endpoint)
    active_custom_rule, rule_details_list = None, []
    
    if rules_summary and rules_summary.get('records'):
        for rule_summary in rules_summary.get('records', []):
            if rule_summary.get('type') == 'Custom':
                 detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_summary['id']}")
                 if detailed_rule:
                     is_enabled = detailed_rule.get('enabled', False)
                     schedule_details, call_action, action_target = parse_rule_details(detailed_rule)
                     rule_details_list.append({
                         'name': detailed_rule.get('name', 'Custom Rule'),
                         'active': is_enabled,
                         'details': [f"<b>Schedule:</b> {schedule_details}", f"<b>Action:</b> {call_action} → {action_target}"]
                     })
                     if is_enabled:
                         active_custom_rule = detailed_rule
                         
    main_node_details = [f"Type: {ext_type} (Ext: {ext_number})"]
    if active_custom_rule:
        # We add the override name to the main box, but the rule itself will be rendered on the side
        main_node_details.append(f"<b>Override:</b> {active_custom_rule.get('name')} (Active)")
    
    current_node_data = {'id': f"N{node_counter}",'type': 'queue','name': ext_name,'details': main_node_details,'rules': rule_details_list,'members': [],'members_name': '','next_ext_id': None}
    flow_data.append(current_node_data)
    next_node_counter, next_ext_id = node_counter + 1, None
    
    # --- FIX: More reliable "duck typing" check for Call Queues ---
    # We check if we can get queue members. If so, it's a queue, regardless of its 'type'.
    bh_rule = rc_api_call(f"{rules_endpoint}/business-hours-rule")
    members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")

    if members_resp is not None: # This indicates it's a queue-like extension
        flow_data[-1]['details'].append(get_business_hours_summary(ext_id))
        flow_data[-1]['details'].append(f"<b>Ring Type:</b> {bh_rule.get('queue', {}).get('transferMode', 'N/A')}")
        member_name, member_list = get_queue_members_info(ext_id)
        flow_data[-1]['members'], flow_data[-1]['members_name'] = member_list, member_name

        if bh_rule:
            queue_details = bh_rule.get('queue', {})
            transfer_ext = bh_rule.get('transfer', {}).get('extension', {})
            overflow_details = [
                f"<b>Agents ring for:</b> {queue_details.get('agentTimeout', 'N/A')} seconds.",
                f"<b>Queue Full ({queue_details.get('maxCallers', 'N/A')} callers):</b> {queue_details.get('maxCallersAction', 'N/A')}",
                f"<b>Wait Timeout ({queue_details.get('holdTime', 'N/A')}s):</b> {queue_details.get('holdTimeExpirationAction', 'N/A')}"
            ]
            flow_data.append({'id': f"N{next_node_counter}",'type': 'queue','name': 'Overflow Rules','details': overflow_details})
            next_node_counter += 1
            if queue_details.get('holdTimeExpirationAction') == 'TransferToExtension' and transfer_ext.get('id'):
                next_ext_id = transfer_ext['id']
                
        after_hours_rule = rc_api_call(f"{rules_endpoint}/after-hours-rule")
        if after_hours_rule and after_hours_rule.get('enabled'):
             schedule_details, call_action, action_target = parse_rule_details(after_hours_rule)
             flow_data.append({'id': f"N{next_node_counter}",'type': 'endpoint','name': 'After Hours Action','details': [f"<b>Action:</b> {call_action} → {action_target}"]})
             next_node_counter += 1
    elif ext_type == 'IvrMenu':
        ivr_details = []
        for prompt in ext_info.get('prompts', []):
            target_ext = prompt.get('extension', {})
            if prompt.get('action') == 'Connect' and target_ext.get('id'):
                key = prompt.get('key', 'Any')
                ivr_details.append(f"<b>Key {key}</b> → Ext: {target_ext.get('extensionNumber', 'N/A')}")
                next_ext_id = target_ext['id']
        flow_data.append({'id': f"N{next_node_counter}",'type': 'queue','name': 'IVR Keypresses','details': ivr_details})
        next_node_counter += 1
        
    if next_ext_id and next_ext_id not in processed_extensions:
        return trace_flow_recursive(next_ext_id, next_node_counter, flow_data, processed_extensions)

    return next_node_counter, flow_data

# --- FIX: PHONE NUMBERS ENDPOINT WITH ACCURATE PARSING ---
@app.route('/api/rc/phone-numbers', methods=['GET'])
def get_phone_numbers():
    """
    Fetches the list of phone numbers and extensions that can be visualized.
    Uses the accurate API schema and error reporting logic.
    """
    if not is_authenticated(): return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    rc_token = get_rc_access_token()
    if not rc_token: return jsonify({'status': 'error', 'message': 'RingCentral not connected. Please connect on the Authenticator tab.'}), 401

    # --- Fetch Phone Numbers from RingCentral ---
    # Using perPage=1000 to maximize results in one call.
    response_data = rc_api_call("/restapi/v1.0/account/~/phone-number?perPage=1000") 
    
    if response_data is None:
        last_log = session.get('api_log', [{}])[-1]
        error_code = last_log.get('code', 'N/A')
        error_detail = last_log.get('detail', 'No details available.')
        
        return jsonify({'status': 'error', 'message': f'RC API Failed. Code: {error_code}. Detail: {error_detail}.'}), 500
    
    if 'records' not in response_data or not isinstance(response_data.get('records'), list):
        return jsonify({'status': 'error', 'message': 'RC API returned success but missing the required "records" list.'}), 500

    # --- Process Successful Response with accurate Schema (Fixes the crash) ---
    
    numbers = []
    VALID_USAGE_TYPES = [
        "MainCompanyNumber", "AdditionalCompanyNumber", "CompanyNumber", 
        "DirectNumber", "CompanyFaxNumber", "ForwardedNumber", 
        "ForwardedCompanyNumber", "ContactCenterNumber", "ConferencingNumber", 
        "MeetingsNumber", "NumberPool", "NumberStorage", 
        "BusinessMobileNumber", "PartnerBusinessMobileNumber", "IntegrationNumber"
    ]

    for record in response_data['records']:
        phone_number = record.get('phoneNumber')
        usage_type = record.get('usageType') 
        ext_info = record.get('extension') 
        
        if usage_type not in VALID_USAGE_TYPES or not phone_number:
            continue
            
        ext_id = None
        
        if ext_info:
            ext_id = ext_info.get('id')
            ext_number = ext_info.get('extensionNumber')
            name = f"Ext: {ext_number}" 
        else:
            ext_id = record.get('id') 
            name = usage_type
            
            # CRITICAL: Only include company numbers that are traceable.
            if usage_type not in ["MainCompanyNumber", "CompanyFaxNumber", "CompanyNumber"]: 
                continue
            
        
        if ext_id and phone_number:
            numbers.append({"id": ext_id, "number": phone_number, "usage": usage_type, "name": name})

    # --- Return Results ---
    if not numbers:
        return jsonify({'status': 'success','numbers': [{"id": "mock1", "number": "+61280000000", "usage": "IVR Menu", "name": "No Live Numbers Found (Mock)"}]}), 200

    return jsonify({'status': 'success','numbers': numbers}), 200

# --- ROUTING: Core Web App ---

@app.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    """Generates the raw flow data structure for the HTML renderer."""
    if not is_authenticated(): return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    if not get_rc_access_token(): return jsonify({'status': 'error', 'message': 'RingCentral not connected.'}), 401

    # FIX: Get the phone number text from the request arguments
    phone_number_text = request.args.get('phoneNumber', f"ID: {ext_id}")
    
    global extension_cache
    extension_cache = {}
    session['api_log'] = [] 
    
    # FIX: Use the phone_number_text for the initial display
    flow_data = [{'id': 'N0', 'type': 'incoming', 'name': 'Incoming Call', 'details': [f"Number: {phone_number_text}"]}]
    processed_extensions = {}
    
    node_counter, flow_data = trace_flow_recursive(ext_id, 1, flow_data, processed_extensions)
    api_log_data = session.pop('api_log', [])
    
    return jsonify({'status': 'success', 'flow_data': flow_data, 'api_log': api_log_data}), 200


@app.route('/')
def index():
    """Serves the main application page with SSR authentication check."""
    authenticated = is_authenticated()
    user_role = session.get('is_admin', False)
    rc_redirect_uri_clean = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback").rstrip('/')
    
    return render_template('index.html', 
                           AUTHENTICATED=authenticated, 
                           USER_ROLE='Admin' if user_role else 'User',
                           RC_REDIRECT_URI=rc_redirect_uri_clean,
                           current_tab=request.args.get('tab', 'authenticator'))

@app.route('/logout')
def logout():
    """Logs the user out and clears the entire session."""
    response = make_response(redirect(url_for('index')))
    session.clear()
    response.delete_cookie(app.config['SESSION_COOKIE_NAME'])
    return response

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Handles the website login via email and shared passcode."""
    config = get_config_from_firestore()
    if not config: return jsonify({'status': 'error', 'message': 'Server Error.'}), 500
        
    expected_passcode = config['passcode']
    admin_emails = config['admin_list']
    
    try: data = request.get_json()
    except: return jsonify({'status': 'error', 'message': 'Invalid format.'}), 400
    
    user_email = data.get('email', '').strip().lower()
    passcode_attempt = data.get('passcode', '').strip()

    if passcode_attempt != expected_passcode: return jsonify({'status': 'error', 'message': 'Invalid Passcode.'}), 401

    session['authenticated'] = True
    session['user_email'] = user_email
    session['is_admin'] = user_email in admin_emails
    session.modified = True
    
    return jsonify({'status': 'success', 'redirect_url': url_for('index')}), 200

@app.route('/auth/initiate-pkce', methods=['POST'])
def initiate_pkce():
    """Initiates the PKCE flow."""
    if not is_authenticated(): return jsonify({'status': 'error', 'message': 'Not unlocked.'}), 401
    
    try: client_id = request.get_json().get('client_id')
    except: return jsonify({'status': 'error', 'message': 'Missing client_id.'}), 400

    if not client_id: return jsonify({'status': 'error', 'message': 'Client ID is required.'}), 400

    code_verifier, code_challenge = create_pkce_challenge()
    
    session['rc_client_id'] = client_id
    session['rc_code_verifier'] = code_verifier
    session['rc_state'] = secrets.token_urlsafe(16)
    
    redirect_uri = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback")
    # FIX: Corrected scope from CallLog to ReadCallLog
    scope_value = os.getenv("RC_SCOPE", "ReadAccounts ReadCallLog")
    
    params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,'code_challenge': code_challenge, 'code_challenge_method': 'S256','scope': scope_value, 'state': session['rc_state']}
    
    auth_url = 'https://platform.ringcentral.com/restapi/oauth/authorize?' + urlencode(params)
    return jsonify({'status': 'success', 'redirect_url': auth_url}), 200

@app.route('/auth/callback', methods=['GET'])
def auth_callback():
    """Handles the token exchange."""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error: return render_template('error.html', message=f"Auth Error: {error}"), 400
    if state != session.get('rc_state'): return render_template('error.html', message="State mismatch."), 403

    client_id = session.pop('rc_client_id', None)
    code_verifier = session.pop('rc_code_verifier', None)
    session.pop('rc_state', None)

    if not code or not client_id or not code_verifier: return render_template('error.html', message="PKCE flow failed: Missing session context."), 400

    redirect_uri = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback")
    global RC_SERVER_URL
    token_url = f"{RC_SERVER_URL}/restapi/oauth/token"
    
    data = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,'code_verifier': code_verifier, 'client_id': client_id}
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    try:
        response = requests.post(token_url, data=data, headers=headers) 
        response.raise_for_status() 
        token_data = response.json()
        
        session['rc_access_token'] = token_data.get('access_token')
        session['rc_current_client_id'] = client_id
        session['rc_user_email'] = token_data.get('owner_id')
        
        return redirect(url_for('index', tab='authenticator'))
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text if e.response is not None else 'No body.'
        
        try: error_details = e.response.json()
        except: error_details = {}

        error_message = error_details.get('error_description', response_text)
        full_message = f"Token exchange failed. Status: {status_code}. Detail: {error_message}"
        return render_template('error.html', message=full_message), 500


@app.route('/api/rc/disconnect', methods=['POST'])
def rc_disconnect():
    """Clears only the RC token state."""
    session.pop('rc_access_token', None)
    session.pop('rc_current_client_id', None)
    session.pop('rc_user_email', None)
    return jsonify({'status': 'success', 'message': 'Disconnected.'}), 200


@app.route('/api/auth/status')
def get_auth_status():
    """API endpoint to check current website login status."""
    return jsonify({'authenticated': session.get('authenticated', False), 'is_admin': session.get('is_admin', False), 'user_email': session.get('user_email', None)}), 200

@app.route('/api/rc/status')
def get_rc_status():
    """API endpoint to check current RingCentral connection status."""
    token = get_rc_access_token()
    return jsonify({'status': 'connected' if token else 'disconnected','client_id': session.get('rc_current_client_id'),'rc_user_email': session.get('rc_user_email')}), 200

@app.route('/api/rc/sip-fetcher', methods=['POST'])
def sip_fetcher_endpoint():
    """Placeholder function guarded by website and RC connection checks."""
    if not is_authenticated(): return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    rc_token = get_rc_access_token()
    if not rc_token: return jsonify({'status': 'error', 'message': 'RingCentral not connected. Please connect above.'}), 401

    return jsonify({'status': 'success', 'result': 'SIP Fetcher placeholder executed successfully.'}), 200


# --- Start the application ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080)) 
    app.run(host="0.0.0.0", port=port, debug=True)