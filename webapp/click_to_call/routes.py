import requests
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage

click_to_call_bp = Blueprint(
    'click_to_call_bp', __name__,
    url_prefix='/api/click_to_call'
)

@click_to_call_bp.route('/dial', methods=['POST'])
@require_rc_token
@track_usage('Click to Call Demo')
def dial_number():
    """Triggers a 2-legged RingCX outbound call using an explicit username."""
    data = request.get_json()
    
    destination = data.get('destination')
    username = data.get('username')
    caller_id = data.get('callerId')
    ring_duration = data.get('ringDuration', 30)
    
    ringcx_token = session.get('ringcx_access_token')
    account_id = str(session.get('ringcx_account_id'))
    
    if not ringcx_token or not account_id:
        return jsonify({'error': 'Not connected to RingCX. Please click Connect to RingCX first.'}), 401
        
    if not destination or not username or not caller_id:
        return jsonify({'error': 'RingCX Username, Destination, and Caller ID are strictly required.'}), 400

    # Trigger the RingCX call
    call_url = f'https://engage.ringcentral.com/voice/api/v1/admin/accounts/{account_id}/activeCalls/createManualAgentCall'
    headers = {
        'Authorization': f'Bearer {ringcx_token}', 
        'Accept': 'application/json'
    }
    
    # These MUST be passed as query parameters (params=payload), not JSON.
    payload = {
        'username': username,
        'destination': destination,
        'callerId': caller_id,
        'ringDuration': ring_duration
    }
    
    try:
        resp = requests.post(call_url, headers=headers, params=payload)
        
        # EngageVoice returns the raw text "true" on success, not JSON
        if resp.ok and resp.text == 'true':
            return jsonify({'status': 'success', 'resolved_username': username})
            
        if not resp.ok:
            return jsonify({'error': f"RingCX API Error: {resp.text}"}), resp.status_code
            
        return jsonify({'status': 'success', 'data': resp.text, 'resolved_username': username})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
