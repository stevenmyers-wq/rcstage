from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)

# --- STEP 1: Get the list of all extensions (Fast) ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    # Fetch all enabled User extensions
    params = {'status': 'Enabled', 'type': 'User', 'perPage': 1000}
    resp = rc_api_call('/restapi/v1.0/account/~/extension', params)
    
    if not resp or 'records' not in resp:
        return jsonify({"error": "Failed to fetch extensions"}), 500

    targets = []
    for record in resp['records']:
        targets.append({
            "id": record['id'],
            "name": record.get('name', 'Unknown'),
            "ext": record.get('extensionNumber', 'N/A')
        })
    
    return jsonify({"targets": targets})

# --- STEP 2: Check one single extension (The "Heavy" work) ---
@notifications_bp.route('/api/notifications/check-single', methods=['POST'])
@require_rc_token
def check_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    # Call RingCentral to get notification settings for this specific user
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    settings = rc_api_call(endpoint)
    
    # Basic logic: Check if email notifications are set
    status_msg = "OK"
    if settings:
        emails = settings.get('emailAddresses', [])
        if not emails:
            status_msg = "MISSING_EMAIL"
    
    return jsonify({
        "status": "success", 
        "ext_id": ext_id,
        "result": status_msg
    })
