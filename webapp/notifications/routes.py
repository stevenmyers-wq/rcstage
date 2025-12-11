from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- 1. GET LIST (Pagination + Robust Filtering) ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        # Fetch EVERYTHING. We will filter in Python to avoid API parameter errors.
        params = {
            'perPage': 1000, 
            'page': page
        }
        
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
        
        # Safety check: If API fails or returns empty, stop looping
        if not resp or 'records' not in resp or not resp['records']:
            break
            
        for record in resp['records']:
            # --- FILTER LOGIC ---
            # 1. Check Status: We want 'Enabled' AND 'NotActivated'
            status = record.get('status', '')
            if status not in ['Enabled', 'NotActivated']:
                continue
                
            # 2. Check Type: We want 'User' (people) AND 'Department' (Call Queues)
            r_type = record.get('type', '')
            if r_type not in ['User', 'Department']:
                continue

            # If we pass both checks, add to list
            targets.append({
                "id": record['id'],
                "name": record.get('name', 'Unknown'),
                "ext": record.get('extensionNumber', 'N/A'),
                "email": record.get('contact', {}).get('email', ''),
                "type": r_type
            })
        
        # Check if there is a next page
        navigation = resp.get('navigation', {})
        if not navigation.get('nextPage'):
            break
            
        page += 1
    
    # Sort by extension number (handle non-numeric gracefully)
    try:
        targets.sort(key=lambda x: int(x['ext']) if x['ext'].isdigit() else 999999)
    except:
        pass 
    
    return jsonify({"targets": targets})

# --- 2. AUDIT SINGLE EXTENSION (Read) ---
@notifications_bp.route('/api/notifications/audit-single', methods=['POST'])
@require_rc_token
def audit_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    # Fetch notification settings
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    settings = rc_api_call(endpoint)
    
    # Handle queues/extensions that have NO notification settings (return empty safe data)
    if not settings:
        return jsonify({
            "status": "success",
            "data": {
                "Extension ID": ext_id,
                "Emails": "",
                "SMS Emails": "",
                "Advanced Mode": "False"
            }
        })

    # Extract useful fields for the CSV
    email_addresses = settings.get('emailAddresses', [])
    sms_addresses = settings.get('smsEmailAddresses', [])
    
    return jsonify({
        "status": "success",
        "data": {
            "Extension ID": ext_id,
            "Emails": "; ".join(email_addresses) if email_addresses else "",
            "SMS Emails": "; ".join(sms_addresses) if sms_addresses else "",
            "Advanced Mode": str(settings.get('advancedMode', False))
        }
    })

# --- 3. UPDATE SINGLE EXTENSION (Write) ---
@notifications_bp.route('/api/notifications/update-single', methods=['POST'])
@require_rc_token
def update_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    new_emails = data.get('emails', []) # Expecting list of strings
    
    # payload structure for RingCentral
    payload = {
        "emailAddresses": new_emails
    }
    
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    resp = rc_api_call(endpoint, method='PUT', payload=payload)
    
    if resp and 'uri' in resp:
        return jsonify({"status": "success"})
    else:
        # Include error message for debugging
        msg = resp.get('message', 'Update failed') if resp else 'Unknown error'
        return jsonify({"status": "error", "message": msg})
