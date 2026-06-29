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
    ring_duration = data.get('ringDuration', 30)
    
    # The agent's RingCX username is typically their RingEX email (from SSO)
    username = session.get('user_email', '')
    
    ringcx_token = session.get('ringcx_access_token')
    account_id = session.get('ringcx_account_id')
    
    if not ringcx_token or not account_id:
        return jsonify({'error': 'Not connected to RingCX. Please connect first.'}), 401
        
    if not destination:
        return jsonify({'error': 'Destination phone number is required.'}), 400
        
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
    
    try:
        # Using requests directly as this hits engage.ringcentral.com, not platform.ringcentral.com
        resp = requests.post(url, headers=headers, params=params)
        if not resp.ok:
            return jsonify({'error': f"RingCX API Error: {resp.text}"}), resp.status_code
            
        return jsonify({'status': 'success', 'data': resp.json()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
