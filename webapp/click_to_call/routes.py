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
    account_id = str(session.get('ringcx_account_id'))
    
    if not ringcx_token or not account_id:
        return jsonify({'error': 'Not connected to RingCX. Please click Connect to RingCX first.'}), 401
        
    if not destination or not agent_ext or not caller_id:
        return jsonify({'error': 'Agent Extension, Destination, and Caller ID are strictly required.'}), 400

    # 1. Lookup the extension from RingEX
    ext_lookup = rc_api_call('/restapi/v1.0/account/~/extension', params={'extensionNumber': agent_ext})
    
    if not ext_lookup or 'records' not in ext_lookup or len(ext_lookup['records']) == 0:
        return jsonify({'error': f"Could not find extension {agent_ext} in the RingCentral account."}), 404
        
    ext_record = ext_lookup['records'][0]
    rex_email = ext_record.get('contact', {}).get('email')
    ext_internal_id = str(ext_record.get('id', ''))
    
    if not rex_email:
        return jsonify({'error': f"Extension {agent_ext} does not have an email address configured."}), 400

    # 2. Construct the RingCX Username mathematically (The Magic Alias)
    try:
        local_part, domain = rex_email.split('@', 1)
        rcx_username = f"{local_part}+{account_id}_{ext_internal_id}@{domain}"
    except Exception:
        return jsonify({'error': 'Failed to parse extension email address.'}), 500

    # 3. Trigger the RingCX call
    call_url = f'https://engage.ringcentral.com/voice/api/v1/admin/accounts/{account_id}/activeCalls/createManualAgentCall'
    headers = {
        'Authorization': f'Bearer {ringcx_token}', 
        'Accept': 'application/json'
    }
    
    # These MUST be passed as query parameters (params=payload), not JSON.
    payload = {
        'username': rcx_username,
        'destination': destination,
        'callerId': caller_id,
        'ringDuration': ring_duration
    }
    
    try:
        resp = requests.post(call_url, headers=headers, params=payload)
        
        # EngageVoice returns the raw text "true" on success, not JSON
        if resp.ok and resp.text == 'true':
            return jsonify({'status': 'success', 'resolved_username': rcx_username})
            
        if not resp.ok:
            return jsonify({'error': f"RingCX API Error: {resp.text}"}), resp.status_code
            
        return jsonify({'status': 'success', 'data': resp.text, 'resolved_username': rcx_username})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
