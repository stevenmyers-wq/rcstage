import os
import requests
from flask import Blueprint, request, jsonify, session, redirect

CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')
# Cloud Run Base URL
BASE_URL = "https://rcau-api-tools-396158962307.us-central1.run.app"
REDIRECT_URI = f"{BASE_URL}/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Redirect to RingCentral login."""
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target ID required", 400
    
    session['analytics_target_id'] = target_id
    scopes = "Analytics ReadCallLog ReadAccounts"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code and force redirect to Analytics tab."""
    code = request.args.get('code')
    if not code: return "No code returned", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if res.ok:
        session['analytics_token'] = res.json().get('access_token')
        # We use an absolute URL + the hash to ensure the browser switches tabs
        return redirect(f"{BASE_URL}/#business-analytics")
    return f"Token Error: {res.text}", 400

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    """Drops the analytics authorization only."""
    session.pop('analytics_token', None)
    session.pop('analytics_target_id', None)
    return redirect(f"{BASE_URL}/#business-analytics")

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Run queries."""
    token = session.get('analytics_token')
    target_id = session.get('analytics_target_id')
    
    if not token: return jsonify({"error": "AUTH_REQUIRED"}), 401
    
    from webapp.analytics.utils import RCBusinessAnalytics
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    
    data = request.json
    result = rc.fetch_records(
        dimension=data.get('dimension', 'Queues'),
        time_settings={
            "timeZone": "UTC",
            "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
        }
    )
    
    if "error" in result:
        # Return the specific error message from RC (e.g. 'Date range exceeds 24h')
        return jsonify({"error": "RC_API_ERROR", "message": result.get('message', result.get('error_description', 'Unknown error'))}), 400
        
    return jsonify(result)
