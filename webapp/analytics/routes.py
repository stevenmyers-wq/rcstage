import os
import requests
from flask import Blueprint, request, jsonify, session, redirect, render_template_string
from webapp.analytics.utils import RCBusinessAnalytics, get_impersonation_token

CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')
BASE_URL = "https://rcau-api-tools-396158962307.us-central1.run.app"
REDIRECT_URI = f"{BASE_URL}/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Redirect to RC SSO. Fix: Only clear analytics data, not global session."""
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target ID required", 400
    
    # FIX: Do NOT use session.clear() here. It logs you out of the whole site.
    session.pop('analytics_isolated_token_vfinal', None)
    session.pop('analytics_token_scopes', None)
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
    """Step 2: Employee login -> Bridge swap."""
    code = request.args.get('code')
    target_id = session.get('analytics_target_id')
    
    if not code:
        err = request.args.get('error_description', 'Authorization Denied')
        return f"Auth Error: {err}", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    auth_data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=auth_data, auth=(CLIENT_ID, CLIENT_SECRET))
    if not res.ok:
        return f"Token Exchange Error: {res.text}", 400
    
    employee_token = res.json().get('access_token')
    
    # Get the impersonated token and its scopes
    customer_token, scopes = get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['analytics_isolated_token_vfinal'] = customer_token
        session['analytics_token_scopes'] = scopes
        return render_template_string("""
            <html><body><script>window.location.href = "/?tab=analytics#business-analytics";</script></body></html>
        """)
    
    return "Bridge failure. Please check logs.", 403

@analytics_bp.route('/api/analytics/test-connection')
def test_connection():
    """Returns the legal Company Name and verifies the Analytics scope."""
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    scopes = session.get('analytics_token_scopes', "")
    
    if not token: return jsonify({"error": "No Session"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    status_code, info = rc.get_account_identity_proof()
    
    # FIND THE NAME: Check multiple common fields
    name = (
        info.get('contactInfo', {}).get('company') or 
        info.get('serviceInfo', {}).get('contact', {}).get('company') or
        info.get('serviceInfo', {}).get('brand', {}).get('name') or
        "Company Name Unknown"
    )
    
    return jsonify({
        "status": "success" if status_code == 200 else "failed",
        "companyName": name,
        "rcId": info.get('id'),
        "hasAnalyticsScope": "Analytics" in scopes,
        "isMatch": str(info.get('id')) == str(target_id)
    })

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    admin_id = rc.get_super_admin_extension()
    
    data = request.json
    result = rc.fetch_records(
        dimension=data.get('dimension'),
        time_settings={
            "timeZone": data.get('timeZone', 'UTC'),
            "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
        },
        admin_extension_id=admin_id
    )
    return jsonify(result)

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    session.pop('analytics_isolated_token_vfinal', None)
    session.pop('analytics_target_id', None)
    session.pop('analytics_token_scopes', None)
    return redirect("/?tab=analytics#business-analytics")
