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
    """Step 1: Redirect to RC SSO. Clears session to prevent code reuse errors."""
    target_id = request.args.get('targetAccountId')
    if not target_id: return "Target ID required", 400
    
    # Crucial: Reset session to prevent 400 invalid_grant on refreshes
    session.clear() 
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
    """Step 2: Authenticate Employee and Bridge to Customer context."""
    code = request.args.get('code')
    target_id = session.get('analytics_target_id')
    
    if not code:
        err = request.args.get('error_description', 'Authorization Denied')
        return f"Auth Error: {err}", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    auth_data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    # Authenticate the employee
    res = requests.post(token_url, data=auth_data, auth=(CLIENT_ID, CLIENT_SECRET))
    if not res.ok:
        return f"Token Exchange Error (400 invalid_grant): {res.text}", 400
    
    employee_token = res.json().get('access_token')
    
    # Exchange for the impersonated token
    customer_token = get_impersonation_token(employee_token, target_id)
    
    if customer_token:
        session['analytics_isolated_token_vfinal'] = customer_token
        return render_template_string("""
            <html><body><script>window.location.href = "/?tab=analytics#business-analytics";</script></body></html>
        """)
    
    return "Impersonation bridge failed. Check GCP Logs for BRIDGE ERROR.", 403

@analytics_bp.route('/api/analytics/test-connection')
def test_connection():
    """Diagnostic Proof: Returns the Legal Company Name for the current token."""
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    
    if not token:
        return jsonify({"status": "error", "message": "No Active Token found in session"}), 401
    
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    status_code, info = rc.get_account_identity_proof()
    
    if status_code == 200:
        return jsonify({
            "status": "success",
            "company": info.get('contactInfo', {}).get('company', 'Unknown Entity'),
            "ownerId": info.get('id'),
            "isCorrectAccount": str(info.get('id')) == str(target_id)
        })
    
    # If it fails, return the full error so we can see the 'burden of proof' fail reason
    return jsonify({
        "status": "failed", 
        "rc_status_code": status_code,
        "rc_response": info
    }), 400

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
    session.clear()
    return redirect("/?tab=analytics#business-analytics")
