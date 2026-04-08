import os
import requests
from flask import Blueprint, request, jsonify, session, redirect, render_template_string

CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')
BASE_URL = "https://rcau-api-tools-396158962307.us-central1.run.app"
REDIRECT_URI = f"{BASE_URL}/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target Account ID required", 400
    
    session['analytics_target_id'] = target_id
    # Added ReadExtensions so we can find the Super Admin ID
    scopes = "Analytics ReadCallLog ReadAccounts ReadExtensions"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    code = request.args.get('code')
    if not code: return "No code returned", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if res.ok:
        # Isolated token key
        session['analytics_access_token_v2'] = res.json().get('access_token')
        
        # JS Redirect Bridge: Confirmed working
        return render_template_string("""
            <html><body><script>window.location.href = "/?tab=analytics#business-analytics";</script></body></html>
        """)
    
    return f"Token Error: {res.text}", 400

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    token = session.get('analytics_access_token_v2')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED"}), 401
    
    from webapp.analytics.utils import RCBusinessAnalytics
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    
    # 1. IMPERSONATION STEP: Resolve the Super Admin Extension ID
    admin_id = rc.get_super_admin_extension()
    
    # 2. FETCH STEP: Pull data for that specific admin context
    data = request.json
    result = rc.fetch_records(
        dimension=data.get('dimension'),
        time_settings={
            "timeZone": data.get('timeZone', 'UTC'),
            "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
        },
        admin_extension_id=admin_id
    )
    
    if isinstance(result, dict) and "error" in result:
        return jsonify({"error": "PERMISSION_DENIED", "message": result.get('message', 'Forbidden')}), 403
        
    return jsonify(result)

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    session.pop('analytics_access_token_v2', None)
    session.pop('analytics_target_id', None)
    return redirect("/?tab=analytics#business-analytics")
