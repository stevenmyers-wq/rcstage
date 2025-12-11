from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- 1. GET LIST (Pagination & Queues Supported) ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        # Loop through all pages (1000 at a time)
        # We remove 'type' from params to ensure we get Users AND Queues
        params = {
            'perPage': 1000, 
            'page': page,
            'status': 'Enabled,NotActivated' # Fetch both status types
        }
        
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
        
        if not resp or 'records' not in resp or not resp['records']:
            break
            
        for record in resp['records']:
            # --- LOCAL FILTERING ---
            # 1. Include Users AND Call Queues (Department)
            # 2. Status is already filtered by the API call above, but double check doesn't hurt
            if record.get('type') in ['User', 'Department']:
                targets.append({
                    "id": record['id'],
                    "name": record.get('name', 'Unknown'),
                    "ext": record.get('extensionNumber', 'N/A'),
                    "email": record.get('contact', {}).get('email', ''),
                    "type": record.get('type') # Useful for debugging
                })
        
        # Check if there is a next page
        navigation = resp.get('navigation', {})
        if not navigation.get('nextPage'):
            break
            
        page += 1
    
    # Sort by extension number
    # Handle cases where extension might be non-numeric (rare but possible)
    try:
        targets.sort(key=lambda x: int(x['ext']) if x['ext'].isdigit() else 999999)
    except:
        pass # Fallback if sorting fails
    
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
    
    if not settings:
        # Some queues might not have notification settings configured at all
        # We return a polite "empty" response instead of an error to keep the loop going
        return jsonify({
            "status": "success",
            "data": {
                "Extension ID": ext_id,
                "Emails": "N/A (No Settings)",
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
        # Include the error message if possible
        msg = "Update failed"
        if resp and 'message' in resp:
            msg = resp['message']
        return jsonify({"status": "error", "message": msg})
