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
    """Step 1: Redirect to RC SSO. Clears existing tokens first."""
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target ID required", 400
    
    session.pop('analytics_isolated_token_vfinal', None)
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
        return f"Auth Error: {request.args.get('error_description', 'Denied')}", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if not res.ok:
        return f"Token Error: {res.text}", 400
    
    employee_token = res.json().get('access_token')
    
    # Get the impersonated token
    customer_token = get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['analytics_isolated_token_vfinal'] = customer_token
        return render_template_string("""
            <html><body><script>window.location.href = "/?tab=analytics#business-analytics";</script></body></html>
        """)
    
    return "Bridge failure. Please check logs.", 403

@analytics_bp.route('/api/analytics/test-connection')
def test_connection():
    """Returns Legal Company Name for burden of proof via the api subdomain."""
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    
    if not token: return jsonify({"error": "No Session"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    info = rc.get_account_identity()
    
    # Extract legal entity from contactInfo
    company = info.get('contactInfo', {}).get('company', 'Unknown')
    actual_id = info.get('id')
    
    if company != 'Unknown':
        return jsonify({
            "status": "success",
            "company": company,
            "rcId": actual_id,
            "isMatch": str(actual_id) == str(target_id)
        })
    
    return jsonify({"status": "failed", "raw": info}), 400

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Query using Internal Backend Route."""
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
    session.clear()
    return redirect("/?tab=analytics#business-analytics")
