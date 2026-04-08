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
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target ID required", 400
    
    session.pop('analytics_isolated_token_vfinal', None)
    session['analytics_target_id'] = target_id
    
    scopes = "Analytics ReadAccounts ReadCallLog"
    rc_url = f"https://platform.ringcentral.com/restapi/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope={scopes}"
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    code = request.args.get('code')
    target_id = session.get('analytics_target_id')
    if not code: return "Auth Error", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    
    employee_token = res.json().get('access_token')
    customer_token = get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['analytics_isolated_token_vfinal'] = customer_token
        return render_template_string("<html><body><script>window.location.href = '/?tab=analytics#business-analytics';</script></body></html>")
    return "Bridge Failed", 403

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    
    data = request.json
    result = rc.fetch_records(
        dimension=data.get('dimension'),
        time_settings={
            "timeZone": data.get('timeZone', 'UTC'),
            "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
        }
    )
    return jsonify(result)

@analytics_bp.route('/api/analytics/test-connection')
def test_connection():
    """Diagnostic check to prove identity."""
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    if not token: return jsonify({"error": "No token"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    info = rc.get_account_identity_v2()
    company = info.get('contactInfo', {}).get('company', 'Unknown')
    
    return jsonify({
        "status": "success", 
        "company": company, 
        "rcId": info.get('id')
    })

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    session.pop('analytics_isolated_token_vfinal', None)
    return redirect("/?tab=analytics#business-analytics")
