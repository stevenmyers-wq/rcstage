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
    """Step 1: Redirect using ONLY confirmed scopes and store target ID."""
    target_id = request.args.get('targetAccountId')
    if not target_id: 
        return "Target ID required", 400
    
    session['analytics_target_id'] = target_id
    
    # Scopes required for both account discovery and analytics fetching
    scopes = "Analytics ReadCallLog ReadAccounts"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code for Employee token, THEN exchange for Customer token."""
    code = request.args.get('code')
    target_id = session.get('analytics_target_id')
    
    if not code:
        err = request.args.get('error_description', 'Authorization Denied')
        return f"Auth Error: {err}", 400

    # 1. Exchange Code for Initial Employee Token
    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    
    res = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    if not res.ok:
        return f"Initial Token Error: {res.text}", 400
    
    employee_token = res.json().get('access_token')

    # 2. PERFORM THE FIX: Exchange for Impersonation Token
    # This switches the context from 'Employee' to 'Customer Account Admin'
    impersonated_token = get_impersonation_token(employee_token, target_id)
    
    if impersonated_token:
        # Store the customer-scoped token for the API calls
        session['analytics_isolated_token_vfinal'] = impersonated_token
        
        # Bridge forces the browser to the correct tab on arrival
        return render_template_string("""
            <html><body><script>window.location.href = "/?tab=analytics#business-analytics";</script></body></html>
        """)
    
    return "Impersonation failed. Ensure you have access to this account in the internal RC portal.", 403

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Discover admin and query records using the impersonated token."""
    token = session.get('analytics_isolated_token_vfinal')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED"}), 401
    
    # Initialize the client with the exchanged token
    rc = RCBusinessAnalytics(account_id=target_id, token=token)
    
    # Discovery step: This will now succeed (no 404)
    admin_id = rc.get_super_admin_extension()
    
    data = request.json
    # Fetch step: This will now succeed (no 403)
    result = rc.fetch_records(
        dimension=data.get('dimension'),
        time_settings={
            "timeZone": data.get('timeZone', 'UTC'),
            "timeRange": {
                "timeFrom": data.get('timeFrom'), 
                "timeTo": data.get('timeTo')
            }
        },
        admin_extension_id=admin_id
    )
    return jsonify(result)

@analytics_bp.route('/api/analytics/logout')
def analytics_logout():
    session.pop('analytics_isolated_token_vfinal', None)
    session.pop('analytics_target_id', None)
    return redirect("/?tab=analytics#business-analytics")
