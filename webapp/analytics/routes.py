import os
import requests
from flask import Blueprint, request, jsonify, session, redirect

CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')
BASE_URL = "https://rcau-api-tools-396158962307.us-central1.run.app"
REDIRECT_URI = f"{BASE_URL}/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Redirect to RingCentral for Analytics specifically."""
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
    """Step 2: Exchange code and return to Analytics tab with query param."""
    code = request.args.get('code')
    if not code: return "No code returned", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if res.ok:
        # USE UNIQUE SESSION KEY - DO NOT USE 'rc_access_token'
        session['analytics_access_token'] = res.json().get('access_token')
        # Redirect with query param for the frontend to handle
        return redirect(f"{BASE_URL}/?tab=analytics#business-analytics")
    
    return f"Token Error: {res.text}", 400

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    """Drops ONLY the analytics session."""
    session.pop('analytics_access_token', None)
    session.pop('analytics_target_id', None)
    return redirect(f"{BASE_URL}/?tab=analytics#business-analytics")

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Query using the isolated token."""
    token = session.get('analytics_access_token')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED", "message": "Analytics authentication missing."}), 401
    
    from webapp.analytics.utils import RCBusinessAnalytics
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    
    data = request.json
    result = rc.fetch_records(
        dimension=data.get('dimension'),
        time_settings={
            "timeZone": "UTC",
            "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
        }
    )
    return jsonify(result)
