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
    """Triggers a 2-legged RingCX outbound call."""
    data = request.get_json()
    
    destination = data.get('destination')
    username = data.get('username')
    caller_id = data.get('callerId')
    ring_duration = data.get('ringDuration', 30)
    
    # We still need the RingCX session token to make Engage Voice API calls
    ringcx_token = session.get('ringcx_access_token')
    account_id = session.get('ringcx_account_id')
    
    if not ringcx_token or not account_id:
        return jsonify({'error': 'Not connected to RingCX. Please click Connect to RingCX first.'}), 401
        
    if not destination or not username:
        return jsonify({'error': 'Destination phone number and Agent Username are required.'}), 400
        
    url = f'https://engage.ringcentral.com/voice/api/v1/admin/accounts/{account_id}/activeCalls'
    headers = {
        'Authorization': f'Bearer {ringcx_token}', 
        'Content-Type': 'application/json'
    }
    
    params = {
        'username': username,
        'destination': destination,
        'ringDuration': ring_duration
    }
    
    if caller_id:
        params['callerId'] = caller_id
    
    try:
        resp = requests.post(url, headers=headers, params=params)
        if not resp.ok:
            return jsonify({'error': f"RingCX API Error: {resp.text}"}), resp.status_code
            
        return jsonify({'status': 'success', 'data': resp.json()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
