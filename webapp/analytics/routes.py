import os
import requests
from flask import Blueprint, request, jsonify, session, redirect, render_template_string

CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')
# Use absolute URL for the callback
BASE_URL = "https://rcau-api-tools-396158962307.us-central1.run.app"
REDIRECT_URI = f"{BASE_URL}/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Save target and redirect to RingCentral."""
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
    """Step 2: Exchange code and return to dashboard with forced tab state."""
    code = request.args.get('code')
    if not code: return "No code returned", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if res.ok:
        # STRICT ISOLATION: Use a unique key so we don't overwrite PKCE token
        session['analytics_isolated_token'] = res.json().get('access_token')
        
        # Absolute redirect with a specific query param for the Tab Enforcer
        return redirect(f"{BASE_URL}/?active_tab=analytics#business-analytics")
    
    return f"Token Error: {res.text}", 400

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    """Drops only the analytics keys."""
    session.pop('analytics_isolated_token', None)
    session.pop('analytics_target_id', None)
    return redirect("/?active_tab=analytics#business-analytics")

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Query records. Wrapped in try/except to ensure JSON output."""
    try:
        token = session.get('analytics_isolated_token')
        target_id = session.get('analytics_target_id')
        
        if not token: 
            return jsonify({"error": "AUTH_REQUIRED", "message": "Analytics session expired."}), 401
        
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
        
        # If the utility returned an error dict, pass it through
        if "error" in result:
            return jsonify(result), 400
            
        return jsonify(result)
        
    except Exception as e:
        # Catch-all to prevent HTML 500 error pages
        return jsonify({"error": "SERVER_EXCEPTION", "message": str(e)}), 500
