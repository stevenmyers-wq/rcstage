from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- 1. GET LIST (Standard) ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        params = {'perPage': 1000, 'page': page}
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
        
        if not resp or 'records' not in resp or not resp['records']:
            break
            
        for record in resp['records']:
            # Local Filter: Enabled/NotActivated AND User/Department
            status = record.get('status', '')
            if status not in ['Enabled', 'NotActivated']:
                continue
            r_type = record.get('type', '')
            if r_type not in ['User', 'Department']:
                continue

            targets.append({
                "id": record['id'],
                "name": record.get('name', 'Unknown'),
                "ext": record.get('extensionNumber', 'N/A'),
                "email": record.get('contact', {}).get('email', ''),
                "type": r_type
            })
        
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
    
    try:
        targets.sort(key=lambda x: int(x['ext']) if x['ext'].isdigit() else 999999)
    except:
        pass 
    
    return jsonify({"targets": targets})

# --- 2. AUDIT SINGLE (Includes True/False Toggles) ---
@notifications_bp.route('/api/notifications/audit-single', methods=['POST'])
@require_rc_token
def audit_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    settings = rc_api_call(endpoint)
    
    if not settings:
        return jsonify({"status": "success", "data": {}})

    # Helper for extracting Email Lists
    def get_emails(obj, key):
        if not obj or key not in obj: return ""
        return "; ".join(obj[key].get('emailAddresses', []))

    # Helper for extracting True/False Toggles (notifyByEmail)
    def get_flag(obj, key):
        if not obj or key not in obj: return "FALSE"
        return str(obj[key].get('notifyByEmail', False)).upper()

    # Common Settings
    basic_emails = "; ".join(settings.get('emailAddresses', []))
    is_advanced = settings.get('advancedMode', False)
    
    # We capture both the EMAILS and the TOGGLES
    return jsonify({
        "status": "success",
        "data": {
            "Extension ID": ext_id,
            "Advanced Mode": str(is_advanced).upper(),
            # The Main "Specified Emails" list (used for Queues & Basic Users)
            "Global Emails": basic_emails,
            
            # Voicemail
            "Enable Voicemail": get_flag(settings, 'voicemails'),
            "Voicemail Emails": get_emails(settings, 'voicemails') if is_advanced else "",
            
            # Missed Calls
            "Enable MissedCalls": get_flag(settings, 'missedCalls'),
            "MissedCall Emails": get_emails(settings, 'missedCalls') if is_advanced else "",
            
            # Faxes
            "Enable Faxes": get_flag(settings, 'inboundFaxes'),
            "Fax Emails": get_emails(settings, 'inboundFaxes') if is_advanced else "",
            
            # SMS
            "Enable SMS": get_flag(settings, 'inboundTexts'),
            "SMS Emails": get_emails(settings, 'inboundTexts') if is_advanced else ""
        }
    })

# --- 3. UPDATE SINGLE (Handles Toggles & Emails) ---
@notifications_bp.route('/api/notifications/update-single', methods=['POST'])
@require_rc_token
def update_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    # 1. Determine Mode
    # Queues are always considered "Basic" (Advanced Mode = False)
    advanced_mode = str(data.get('advanced_mode', 'FALSE')).upper() == 'TRUE'
    
    # 2. Helper to parse booleans from CSV strings like "TRUE"/"FALSE"
    def parse_bool(val):
        return str(val).upper() == 'TRUE'
    
    # 3. Helper to parse email lists
    def parse_list(val):
        if not val: return []
        return [e.strip() for e in val.split(';') if e.strip()]

    # 4. Construct Payload
    payload = {
        "advancedMode": advanced_mode,
        
        # Determine toggles (These apply to both Basic and Advanced)
        "voicemails": {
            "notifyByEmail": parse_bool(data.get('enable_vm'))
        },
        "missedCalls": {
            "notifyByEmail": parse_bool(data.get('enable_missed'))
        },
        "inboundFaxes": {
            "notifyByEmail": parse_bool(data.get('enable_fax'))
        },
        "inboundTexts": {
            "notifyByEmail": parse_bool(data.get('enable_sms'))
        }
    }

    # 5. Handle Email Addresses based on Mode
    if not advanced_mode:
        # BASIC / QUEUE: One global list applies to all enabled types
        payload["emailAddresses"] = parse_list(data.get('global_emails'))
    else:
        # ADVANCED USER: Specific lists for each type
        # Note: We also set the email list inside the specific objects
        payload["voicemails"]["emailAddresses"] = parse_list(data.get('vm_emails'))
        payload["missedCalls"]["emailAddresses"] = parse_list(data.get('missed_emails'))
        payload["inboundFaxes"]["emailAddresses"] = parse_list(data.get('fax_emails'))
        payload["inboundTexts"]["emailAddresses"] = parse_list(data.get('sms_emails'))

    # 6. Send Update
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    resp = rc_api_call(endpoint, method='PUT', payload=payload)
    
    if resp and 'uri' in resp:
        return jsonify({"status": "success"})
    else:
        msg = resp.get('message', 'Update failed') if resp else 'Unknown error'
        return jsonify({"status": "error", "message": msg})
