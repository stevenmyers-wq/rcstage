import os
import io
import json
import base64
import hashlib
import secrets
import requests
import pandas as pd
from urllib.parse import urlencode
from flask import Blueprint, jsonify, request, send_file, session, redirect, current_app, Response, stream_with_context
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.usage_tracking import track_usage
from . import utils

cq_hours_bp = Blueprint('cq_hours', __name__, url_prefix='/api/cq_hours')

def create_pkce_challenge():
    code_verifier = secrets.token_urlsafe(96)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode('ascii')
    return code_verifier, code_challenge

def get_strict_redirect_uri():
    return os.getenv('CQ_REDIRECT_URI', 'http://localhost:8080/api/cq_hours/oauth2callback')

@cq_hours_bp.route('/auth', methods=['GET'])
def cq_auth():
    code_verifier, code_challenge = create_pkce_challenge()
    session['cq_code_verifier'] = code_verifier
    
    redirect_uri = get_strict_redirect_uri()
    client_id = os.getenv('SM_CLIENT_ID')
    
    if not client_id:
        return "SM_CLIENT_ID not found in environment variables.", 500
    
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'state': 'cq_hours'
    }
    
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    auth_url = f"{base_url}/restapi/oauth/authorize?{urlencode(params)}"
    return redirect(auth_url)

@cq_hours_bp.route('/oauth2callback', methods=['GET'])
def cq_oauth2callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
        
    redirect_uri = get_strict_redirect_uri()
    code_verifier = session.pop('cq_code_verifier', None)
    
    client_id = os.getenv('SM_CLIENT_ID')
    client_secret = os.getenv('SM_CLIENT_SECRET')
    
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
        session['cq_employee_token'] = token_data.get('access_token')
        return redirect("/?tab=cq_hours")
    else:
        return jsonify({"error": "Failed to exchange code", "details": response.json()}), 400

@cq_hours_bp.route('/bridge', methods=['POST'])
def create_bridge():
    data = request.json
    target_id = data.get('targetAccountId')
    
    if not target_id:
        return jsonify({"error": "Target Account ID is required"}), 400
        
    employee_token = session.get('cq_employee_token')
    if not employee_token:
        return jsonify({"error": "Not authenticated. Please Sign In first."}), 401
        
    customer_token = utils.get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['cq_isolated_token'] = customer_token
        session['cq_target_id'] = target_id
        return jsonify({"success": True})
    
    return jsonify({"error": "Impersonation Bridge Failed. Ensure you are logged in and the target ID is valid."}), 403

@cq_hours_bp.route('/logout')
def cq_logout():
    session.pop('cq_isolated_token', None)
    session.pop('cq_target_id', None)
    session.pop('cq_employee_token', None)
    return redirect("/?tab=cq_hours")

@cq_hours_bp.route('/template', methods=['GET'])
def download_template():
    df = pd.DataFrame([
        {
            "Queue Name": "Support Queue",
            "Record Group Name": "",
            "Extension": "1001",
            "Site": "Main Site",
            "Status": "Enabled",
            "Phone Number": "",
            "Queue Manager": "",
            "Queue Email": "support@company.com",
            "Queue PIN": "",
            "Members (Ext)": "2001, 2002, 2003",
            "Timezone": "US/Eastern",
            "Hours": "8:30AM-5:30PM Mon-Fri",
            "Greeting": "",
            "Audio While Connecting": "",
            "Hold Music": "",
            "Interrupt Audio": "Periodically",
            "Interrupt Prompt": "30 Seconds",
            "Ring Type": "Simultaneous",
            "User Ring Time": "20 Seconds",
            "Total Ring Time": "5 Minutes",
            "Wrap Up Time": "15 Seconds",
            "Member Queue Status": "",
            "Callers In Queue": "10",
            "When Queue is Full": "TransferToExtension",
            "Queue Full Destination": "2004",
            "When Max Time is Reached": "Voicemail",
            "Time Reached Destination": "",
            "Voicemail Greeting": "",
            "Voicemail Recipients": "",
            "Voicemail Notifications": "Notify & Attach",
            "Voicemail Notifications Email": "manager@company.com",
            "After Hours Behavior": "TransferToExtension",
            "After Hours Destination": "2004"
        }
    ])
    
    global_timezones = [
        "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii",
        "Canada/Eastern", "Canada/Central", "Canada/Mountain", "Canada/Pacific", "Canada/Atlantic",
        "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Athens", "Europe/Moscow",
        "GMT", "UTC", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Asia/Hong_Kong",
        "Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth",
        "Pacific/Auckland", "America/Sao_Paulo", "America/Buenos_Aires", "America/Mexico_City"
    ]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Queue Config')
        
        tz_df = pd.DataFrame({"ValidTimezones": global_timezones})
        tz_df.to_excel(writer, index=False, sheet_name='Timezone_Ref')
        
        workbook = writer.book
        config_ws = workbook['Queue Config']
        
        dv_tz = DataValidation(type="list", formula1="=Timezone_Ref!$A$2:$A$" + str(len(global_timezones) + 1), allow_blank=True)
        config_ws.add_data_validation(dv_tz)
        dv_tz.add("K2:K1000") 
        
        schema_validations = {
            "E": '"Enabled,Disabled"', "M": '"Default,Custom,Off"', "N": '"Default,Custom,Off"', "O": '"Default,Custom,Off"',
            "P": '"Periodically,Never"', "Q": '"10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute"',
            "R": '"Simultaneous,Sequential,Rotating"', "S": '"10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute,2 Minutes"',
            "T": '"15 Seconds,30 Seconds,45 Seconds,1 Minute,2 Minutes,3 Minutes,4 Minutes,5 Minutes,10 Minutes,15 Minutes"',
            "U": '"0 Seconds,5 Seconds,10 Seconds,15 Seconds,20 Seconds,30 Seconds,1 Minute"', "V": '"Accepting,NotAccepting"',
            "W": '"1,2,3,4,5,10,15,20,25"', "X": '"Voicemail,TransferToExtension,Disconnect,Announcement"',
            "Z": '"Voicemail,TransferToExtension,Disconnect,Announcement"', "AB": '"Default,Custom,Off"',
            "AD": '"Off,Notify by Email,Notify & Attach,Notify Attach & Read"',
            "AF": '"TakeMessagesOnly,TransferToExtension,UnconditionalForwarding,PlayAnnouncementOnly,Disconnect"'
        }

        for col_letter, formula_string in schema_validations.items():
            dv = DataValidation(type="list", formula1=formula_string, allow_blank=True)
            config_ws.add_data_validation(dv)
            dv.add(f"{col_letter}2:{col_letter}1000")

    output.seek(0)
    return send_file(output, as_attachment=True, download_name='CQ_Omni_Manager_Template.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# --- NEW: Lightweight Endpoint to fetch Worksheet names instantly ---
@cq_hours_bp.route('/sheets', methods=['POST'])
def get_sheets():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
        
    file = request.files['file']
    if file.filename.endswith('.csv'):
        return jsonify(["CSV Format (No Sheets)"])
        
    try:
        xls = pd.ExcelFile(file)
        return jsonify(xls.sheet_names)
    except Exception as e:
        return jsonify({"error": f"Failed to parse sheets: {str(e)}"}), 400

@cq_hours_bp.route('/upload', methods=['POST'])
@track_usage('CQ Omni Manager Update')
def upload_hours():
    token = session.get('cq_isolated_token')
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized: Please Bridge the connection first."}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400
        
    is_preview = request.form.get('action') == 'preview'
    sheet_name = request.form.get('sheet_name')
        
    try:
        file = request.files['file']
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            # Safely target the specific sheet if the user provided one
            if sheet_name and sheet_name != 'CSV Format (No Sheets)':
                df = pd.read_excel(file, sheet_name=sheet_name)
            else:
                df = pd.read_excel(file)
                
        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    def generate():
        try:
            for chunk in utils.update_cq_batch(records, token, is_preview=is_preview):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp