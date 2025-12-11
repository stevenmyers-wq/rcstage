from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- 1. GET LIST (For starting the Audit) ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    params = {'status': 'Enabled', 'type': 'User', 'perPage': 1000}
    resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
    
    targets = []
    if resp and 'records' in resp:
        for record in resp['records']:
            targets.append({
                "id": record['id'],
                "name": record.get('name', 'Unknown'),
                "ext": record.get('extensionNumber', 'N/A'),
                "email": record.get('contact', {}).get('email', '')
            })
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
        return jsonify({"status": "error", "message": "API call failed"})

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
        return jsonify({"status": "error", "message": "Update failed"})
