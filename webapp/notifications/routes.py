import time
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

def _call_with_retry(endpoint, method='GET', **kwargs):
    """Helper to handle 429 Rate Limits gracefully within the route (server-side pause)."""
    max_retries = 3
    for _ in range(max_retries):
        resp = rc_api_call(endpoint, method=method, return_response=True, **kwargs)
        
        status = getattr(resp, 'status_code', None)
        if status == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 60
            time.sleep(retry_after + 1)
            continue
            
        if resp and getattr(resp, 'ok', False):
            return resp.json()
            
        return None
    return None

# --- 1. GET LIST ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        # Removed the 'type' API parameter to fetch the full raw list and rely on the Python filter.
        params = {'perPage': 1000, 'page': page}
        resp_data = _call_with_retry('/restapi/v1.0/account/~/extension', params=params)
        
        if not resp_data or 'records' not in resp_data or not resp_data['records']:
            break
            
        for record in resp_data['records']:
            r_type = record.get('type', '')
            if r_type not in ['User', 'Department', 'Voicemail', 'Limited']:
                continue
            
            # Local Filter: Only enforce Enabled/NotActivated on Users and Limited.
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
        
        # Pagination Fix: Use paging.totalPages instead of navigation.nextPage
        paging = resp_data.get('paging', {})
        if page >= paging.get('totalPages', 1):
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
    
    # Use return_response=True so we can pass 429s directly to the frontend for UI pauses
    resp = rc_api_call(endpoint, return_response=True)
    
    if resp and getattr(resp, 'status_code', None) == 429:
        retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 60
        return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        
    if not resp or not getattr(resp, 'ok', False):
        return jsonify({"status": "error", "message": "Settings unavailable or unassigned"})

    settings = resp.json()

    def get_emails(obj, key):
        if not obj or key not in obj: return ""
        return "; ".join(obj[key].get('emailAddresses', []))

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
        "Global Emails": "; ".join(settings.get('emailAddresses', [])),
        "Voicemail Emails": get_emails(settings, 'voicemails'),
        "Fax Emails": get_emails(settings, 'inboundFaxes'),
        "SMS Emails": get_emails(settings, 'inboundTexts'),
        "MissedCall Emails": get_emails(settings, 'missedCalls')
    }

    return jsonify({"status": "success", "data": response_data})


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
    
    original_resp = rc_api_call(endpoint, return_response=True)
    if original_resp and getattr(original_resp, 'status_code', None) == 429:
        retry_after = int(original_resp.headers.get('Retry-After', 60)) if hasattr(original_resp, 'headers') else 60
        return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        
    if not original_resp or not getattr(original_resp, 'ok', False):
        return jsonify({"status": "error", "message": "Failed to fetch original settings"})

    original = original_resp.json()
    advanced_mode = str(data.get('advanced_mode', 'FALSE')).upper() == 'TRUE'
    payload = {}
    
    if 'advancedMode' in original:
        payload['advancedMode'] = advanced_mode

    if 'global_emails' in data:
        payload["emailAddresses"] = parse_list(data.get('global_emails'))

    for cat in ['voicemails', 'missedCalls', 'inboundFaxes', 'inboundTexts']:
        if cat in original:
            payload[cat] = original[cat] 
            
            if cat == 'voicemails':
                if 'enable_vm' in data: payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_vm'))
                if 'vm_emails' in data: payload[cat]["emailAddresses"] = parse_list(data.get('vm_emails'))
            elif cat == 'missedCalls':
                if 'enable_missed' in data: payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_missed'))
                if 'missed_emails' in data: payload[cat]["emailAddresses"] = parse_list(data.get('missed_emails'))
            elif cat == 'inboundFaxes':
                if 'enable_fax' in data: payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_fax'))
                if 'fax_emails' in data: payload[cat]["emailAddresses"] = parse_list(data.get('fax_emails'))
            elif cat == 'inboundTexts':
                if 'enable_sms' in data: payload[cat]["notifyByEmail"] = parse_bool(data.get('enable_sms'))
                if 'sms_emails' in data: payload[cat]["emailAddresses"] = parse_list(data.get('sms_emails'))

    put_resp = rc_api_call(endpoint, method='PUT', json=payload, return_response=True)
    
    if put_resp and getattr(put_resp, 'status_code', None) == 429:
        retry_after = int(put_resp.headers.get('Retry-After', 60)) if hasattr(put_resp, 'headers') else 60
        return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        
    if put_resp and getattr(put_resp, 'ok', False):
        return jsonify({"status": "success"})
    else:
        msg = "Update failed"
        try: msg = put_resp.json().get('message', msg)
        except: pass
        return jsonify({"status": "error", "message": msg})
