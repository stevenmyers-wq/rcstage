import os
import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, send_file, session
from ringcentral import SDK
from ringcentral.http.api_exception import ApiException

custom_rules_bp = Blueprint('custom_rules', __name__)

# --- HELPER: Time Parser ---
def parse_time_range(range_str):
    if not isinstance(range_str, str) or '-' not in range_str:
        return None
    try:
        start_str, end_str = range_str.split('-')
        fmt_in = "%I:%M %p" 
        fmt_out = "%H:%M"
        start_time = datetime.strptime(start_str.strip(), fmt_in).strftime(fmt_out)
        end_time = datetime.strptime(end_str.strip(), fmt_in).strftime(fmt_out)
        return [{"from": start_time, "to": end_time}]
    except:
        return None

# --- HELPER: Payload Builder ---
def build_rule_payload(row, ext_id):
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    enabled = True if str(row.get('Enabled')).lower() == 'yes' else False
    
    payload = {
        "type": "Custom", "name": rule_name, "enabled": enabled,
        "callers": [], "calledNumbers": [], "schedule": {}
    }

    if pd.notna(row.get('Caller ID')):
        payload['callers'] = [{'callerId': c.strip()} for c in str(row.get('Caller ID')).split(',') if c.strip()]
    if pd.notna(row.get('Called Number')):
        payload['calledNumbers'] = [{'phoneNumber': n.strip()} for n in str(row.get('Called Number')).split(',') if n.strip()]

    schedule = {'weeklyRanges': {}}
    days_map = {'Monday': 'monday', 'Tuesday': 'tuesday', 'Wednesday': 'wednesday',
                'Thursday': 'thursday', 'Friday': 'friday', 'Saturday': 'saturday', 'Sunday': 'sunday'}
    has_schedule = False
    for col, api_key in days_map.items():
        if col in row and pd.notna(row[col]):
            ranges = parse_time_range(row[col])
            if ranges:
                schedule['weeklyRanges'][api_key] = ranges
                has_schedule = True
    if has_schedule:
        payload['schedule'] = schedule

    action_map = {
        'Transfer to External': 'UnconditionalForwarding',
        'Send to Voicemail': 'TakeMessagesOnly',
        'Transfer to Extension': 'TransferToExtension',
        'Play Message': 'PlayAnnouncementOnly',
        'Play Message and Disconnect': 'PlayAnnouncementOnly',
        'Fwd Direct To Main': 'ForwardCalls'
    }
    api_action = action_map.get(row.get('Action'), 'ForwardCalls')
    payload['callHandlingAction'] = api_action
    
    return payload, api_action

# --- HELPER: Auth & RC ---
def get_platform(client_id, client_secret):
    """
    Initializes SDK using the credentials provided in the form.
    Strictly Production.
    """
    # 1. HARDCODED PRODUCTION URL
    server_url = 'https://platform.ringcentral.com'
    
    # Initialize SDK (Clean spaces from inputs)
    sdk = SDK(client_id.strip(), (client_secret or '').strip(), server_url)
    platform = sdk.platform()
    
    # 2. Get Token (Aggressively Clean Spaces)
    stored_token = session.get('tokens')
    
    # Clean inside the dict object
    if stored_token and isinstance(stored_token, dict) and 'access_token' in stored_token:
        stored_token['access_token'] = stored_token['access_token'].strip()
    
    # Fallback to simple string
    if not stored_token:
        simple_token = session.get('rc_access_token') or session.get('oauth_token')
        if simple_token:
            stored_token = {'access_token': simple_token.strip(), 'expires_in': 3600}
    
    if not stored_token:
        raise Exception("No Auth Token found. Please go to 'PKCE Setup' and Log In again.")

    platform.auth().set_data(stored_token)
        
    return platform

def get_extension_id(platform, extension_number):
    try:
        resp = platform.get('/restapi/v1.0/account/~/extension', {'extensionNumber': extension_number})
        records = resp.json().get('records', [])
        return records[0]['id'] if records else None
    except Exception as e:
        raise e 

# --- ROUTES ---

@custom_rules_bp.route('/api/update_rules', methods=['POST'])
def update_rules():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    client_id = request.form.get('client_id')
    client_secret = request.form.get('client_secret')

    if not client_id:
        return jsonify({"error": "Client ID is required."}), 400

    # --- 1. INITIALIZE & AUTH CHECK ---
    try:
        platform = get_platform(client_id, client_secret)
        # Test connection strictly
        platform.get('/restapi/v1.0/account/~/extension', {'perPage': 1})
        
    except ApiException as api_err:
        # --- FIXED ERROR EXTRACTION ---
        status = "Unknown"
        msg = str(api_err)
        
        # Safely extract real RC error details
        if hasattr(api_err, 'response'):
             status = getattr(api_err.response, 'status_code', 'Unknown')
             try:
                 msg = api_err.response.text 
             except: 
                 pass
                 
        return jsonify({
            "error": "RingCentral Rejected Connection",
            "details": f"Status: {status}\nResponse: {msg}\n\nHint: Check if Client ID matches your login."
        }), 401
        
    except Exception as e:
        return jsonify({
            "error": "System/Auth Error",
            "details": str(e)
        }), 401

    # --- 2. FILE PROCESSING ---
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    
    for index, row in df.iterrows():
        ext_num = row.get('Ext Number')
        if pd.isna(ext_num): continue

        try:
            ext_id = get_extension_id(platform, ext_num)
            if not ext_id:
                results.append(f"Row {index}: ⚠️ Extension {ext_num} not found.")
                continue

            payload, action_type = build_rule_payload(row, ext_id)

            if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
                payload['unconditionalForwarding'] = {'phoneNumber': str(row.get('External Number'))}
            elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
                target_id = get_extension_id(platform, row.get('Transfer Extension'))
                if target_id: 
                    payload['transfer'] = {'extension': {'id': target_id}}
                else:
                    results.append(f"⚠️ Target Ext {row.get('Transfer Extension')} not found.")
                    continue
            elif action_type == 'TakeMessagesOnly' and pd.notna(row.get('Voicemail Recipient')):
                vm_id = get_extension_id(platform, row.get('Voicemail Recipient'))
                if vm_id: 
                    payload['voicemail'] = {'recipient': {'id': vm_id}}

            rule_id = row.get('Rule ID')
            if pd.notna(rule_id) and str(rule_id).strip():
                platform.put(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}", payload)
                results.append(f"✅ Updated Rule for Ext {ext_num}")
            else:
                platform.post(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule", payload)
                results.append(f"✅ Created Rule for Ext {ext_num}")
                
        except Exception as e:
            results.append(f"❌ Error Ext {ext_num}: {str(e)}")

    return jsonify({"logs": results})

@custom_rules_bp.route('/template', methods=['GET'])
def download_template():
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 
        'Caller ID', 'Called Number', 'Work or After Hours', 
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 
        'Specific Dates', 'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient'
    ]
    example_data = [{
        'Ext Number': '101', 'Ext Name': 'John Doe', 'Rule Name': 'Holiday Rule',
        'Rule ID': '', 'Enabled': 'Yes', 'Caller ID': '1234567890',
        'Called Number': '', 'Monday': '9:00 AM - 5:00 PM',
        'Action': 'Transfer to External', 'External Number': '15550001234'
    }]
    
    df = pd.DataFrame(example_data, columns=columns)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template')
        worksheet = writer.sheets['Template']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value) or "") for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2
    output.seek(0)
    
    return send_file(output, download_name="custom_rules_template.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
