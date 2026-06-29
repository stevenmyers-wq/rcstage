import requests
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call

click_to_call_bp = Blueprint(
    'click_to_call_bp', __name__,
    url_prefix='/api/click_to_call'
)

@click_to_call_bp.route('/dial', methods=['POST'])
@require_rc_token
@track_usage('Click to Call Demo')
def dial_number():
    data = request.get_json()
    
    destination = data.get('destination')
    agent_ext = data.get('agentExt')
    caller_id = data.get('callerId')
    ring_duration = data.get('ringDuration', 30)
    
    ringcx_token = session.get('ringcx_access_token')
    account_id = session.get('ringcx_account_id')
    
    if not ringcx_token or not account_id:
        return jsonify({'error': 'Not connected to RingCX. Please click Connect to RingCX first.'}), 401
        
    if not destination or not agent_ext:
        return jsonify({'error': 'Destination phone number and Agent Extension are required.'}), 400

    # 1. Lookup the base email from RingEX
    ext_lookup = rc_api_call('/restapi/v1.0/account/~/extension', params={'extensionNumber': agent_ext})
    
    if not ext_lookup or 'records' not in ext_lookup or len(ext_lookup['records']) == 0:
        return jsonify({'error': f"Could not find extension {agent_ext} in the RingCentral account."}), 404
        
    rex_email = ext_lookup['records'][0].get('contact', {}).get('email')
    
    if not rex_email:
        return jsonify({'error': f"Extension {agent_ext} does not have an email address configured."}), 400

    # 2. Query RingCX Users list to find the matching username (FIXED: changed /agents to /users)
    users_url = f'https://engage.ringcentral.com/voice/api/v1/admin/accounts/{account_id}/users'
    headers = {
        'Authorization': f'Bearer {ringcx_token}', 
        'Accept': 'application/json'
    }
    
    try:
        users_resp = requests.get(users_url, headers=headers)
        users_resp.raise_for_status()
        
        # Engage Voice can sometimes wrap arrays or return them directly depending on the version
        resp_json = users_resp.json()
        users_list = resp_json if isinstance(resp_json, list) else resp_json.get('users', [])
    except Exception as e:
        return jsonify({'error': f"Failed to fetch RingCX users: {str(e)}"}), 500
        
    # Match the user by email to extract their exact RingCX username
    rcx_username = None
    for user in users_list:
        if user.get('email', '').lower() == rex_email.lower():
            rcx_username = user.get('username')
            break
            
    if not rcx_username:
        return jsonify({'error': f"Could not find a RingCX agent matching the email {rex_email}."}), 404

    # 3. Trigger the RingCX call
    call_url = f'https://engage.ringcentral.com/voice/api/v1/admin/accounts/{account_id}/activeCalls/createManualAgentCall'
    headers['Content-Type'] = 'application/json'
    
    params = {
        'username': rcx_username,
        'destination': destination,
        'ringDuration': ring_duration
    }
    
    if caller_id:
        params['callerId'] = caller_id
    
    try:
        resp = requests.post(call_url, headers=headers, params=params)
        if not resp.ok:
            return jsonify({'error': f"RingCX API Error: {resp.text}"}), resp.status_code
            
        return jsonify({'status': 'success', 'data': resp.json(), 'resolved_username': rcx_username})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
