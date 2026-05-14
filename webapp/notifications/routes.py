from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- 1. GET LIST ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        # Removed strict status API param to ensure we capture Queues/Voicemail 
        params = {'perPage': 1000, 'page': page}
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
        
        if not resp or 'records' not in resp or not resp['records']:
            break
            
        for record in resp['records']:
            r_type = record.get('type', '')
            if r_type not in ['User', 'Department', 'Voicemail', 'Limited']:
                continue
            
            # Local Filter: Only enforce Enabled/NotActivated on Users and Limited.
            # Queues and Message-Only often have Unassigned statuses.
            status = record.get('status', '')
            if r_type in ['User', 'Limited'] and status not in ['Enabled', 'NotActivated']:
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


# --- 2. AUDIT SINGLE ---
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

    # Helper for extracting Toggles
    def get_flag(obj, key):
        if not obj or key not in obj: return "FALSE"
        return str(obj[key].get('notifyByEmail', False)).upper()

    is_advanced = settings.get('advancedMode', False)
    
    response_data = {
        "Extension ID": ext_id,
        "Advanced Mode": str(is_advanced).upper(),
        "Enable Voicemail": get_flag(settings, 'voicemails'),
        "Enable MissedCalls": get_flag(settings, 'missedCalls'),
        "Enable Faxes": get_flag(settings, 'inboundFaxes'),
        "Enable SMS": get_flag(settings, 'inboundTexts'),
    }

    if not is_advanced:
        # QUEUE / BASIC MODE: Use Global Email only
        response_data["Global Emails"] = "; ".join(settings.get('emailAddresses', []))
        response_data["Voicemail Emails"] = ""
        response_data["Fax Emails"] = ""
        response_data["SMS Emails"] = ""
        response_data["MissedCall Emails"] = ""
    else:
        # ADVANCED MODE: Use Specific Emails only
        response_data["Global Emails"] = "" 
        response_data["Voicemail Emails"] = get_emails(settings, 'voicemails')
        response_data["Fax Emails"] = get_emails(settings, 'inboundFaxes')
        response_data["SMS Emails"] = get_emails(settings, 'inboundTexts')
        response_data["MissedCall Emails"] = get_emails(settings, 'missedCalls')

    return jsonify({
        "status": "success", 
        "data": response_data
    })


# --- 3. UPDATE SINGLE ---
@notifications_bp.route('/api/notifications/update-single', methods=['POST'])
@require_rc_token
def update_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    def parse_bool(val):
        return str(val).upper() == 'TRUE'
    
    def parse_list(val):
        if not val: return []
        return [e.strip() for e in val.split(';') if e.strip()]

    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    
    # 1. Fetch original settings to see what this extension type supports
    original = rc_api_call(endpoint)
    if not original:
        return jsonify({"status": "error", "message": "Failed to fetch original settings"})

    advanced_mode = str(data.get('advanced_mode', 'FALSE')).upper() == 'TRUE'
    
    payload = {}
    
    # Only set advancedMode if the extension supports it (Queues do not)
    if 'advancedMode' in original:
        payload['advancedMode'] = advanced_mode

    if not advanced_mode:
        payload["emailAddresses"] = parse_list(data.get('global_emails'))

    # Only attach properties that are naturally supported by this extension
    for cat in ['voicemails', 'missedCalls', 'inboundFaxes', 'inboundTexts']:
        if cat in original:
            payload[cat] = original[cat] # Inherit original settings like includeManagers
            
            if cat == 'voicemails': payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_vm'))
            elif cat == 'missedCalls': payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_missed'))
            elif cat == 'inboundFaxes': payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_fax'))
            elif cat == 'inboundTexts': payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_sms'))
            
            if advanced_mode:
                if cat == 'voicemails': payload[cat]["emailAddresses"] = parse_list(data.get('vm_emails'))
                elif cat == 'missedCalls': payload[cat]["emailAddresses"] = parse_list(data.get('missed_emails'))
                elif cat == 'inboundFaxes': payload[cat]["emailAddresses"] = parse_list(data.get('fax_emails'))
                elif cat == 'inboundTexts': payload[cat]["emailAddresses"] = parse_list(data.get('sms_emails'))

    resp = rc_api_call(endpoint, method='PUT', payload=payload)
    
    if resp and 'uri' in resp:
        return jsonify({"status": "success"})
    else:
        msg = resp.get('message', 'Update failed') if resp else 'Unknown error'
        return jsonify({"status": "error", "message": msg})
