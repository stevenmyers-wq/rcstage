import os
import requests
from flask import Blueprint, request, jsonify, session, redirect

# GCP Env variables
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

# This MUST match the URI in your RC Developer Console exactly
REDIRECT_URI = "https://rcau-api-tools-396158962307.us-central1.run.app/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Redirect to RingCentral with explicit scopes."""
    target_id = request.args.get('targetAccountId')
    if not target_id:
        return "Missing Target Account ID", 400

    # Cache target ID in the session
    session['analytics_target_id'] = target_id
    
    # We use space-separated technical scope names
    # Mapping "Analytics" -> ReadAnalytics and "Read Call Log" -> ReadCallLog
    scopes = "ReadAnalytics ReadCallLog ReadAccounts"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code for token and return to Analytics tab."""
    # Catch errors from the authorize step (like invalid scope)
    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', 'Unknown Error')
        return f"Authorization Error: {error} - {desc}", 400

    code = request.args.get('code')
    if not code:
        return "Authorization failed: No code returned.", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    
    # Payload for token exchange - redirect_uri must match exactly
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    
    # Handshake using Client ID/Secret as Auth Basic
    response = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    
    if response.ok:
        token_data = response.json()
        # Save token to a unique analytics key
        session['analytics_token'] = token_data.get('access_token')
        # Redirect back to the UI dashboard anchor
        return redirect('/#business-analytics') 
    
    return f"Token Exchange Failed: {response.text}", 400

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Run queries using the isolated analytics token."""
    data = request.json
    token = session.get('analytics_token')
    target_id = session.get('analytics_target_id')
    
    if not token or not target_id:
        return jsonify({"error": "AUTH_REQUIRED"}), 401

    from webapp.analytics.utils import RCBusinessAnalytics
    rc_analytics = RCBusinessAnalytics(account_id=target_id, token=token)

    try:
        result = rc_analytics.fetch_records(
            dimension=data.get('dimension', 'Queues'),
            time_settings={
                "timeZone": data.get('timeZone', 'UTC'),
                "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
            }
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
