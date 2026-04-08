import os
import requests
from flask import Blueprint, request, jsonify, session, redirect

# GCP Env variables
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

# Must match RC Developer Console exactly
REDIRECT_URI = "https://rcau-api-tools-396158962307.us-central1.run.app/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Redirect to RingCentral with the corrected scope string."""
    target_id = request.args.get('targetAccountId')
    if not target_id:
        return "Missing Target Account ID", 400

    # Store target ID in session for the callback
    session['analytics_target_id'] = target_id
    
    # Updated scopes: Using "Analytics" instead of "ReadAnalytics"
    # These technical names correspond to the tags in your screenshot
    scopes = "Analytics ReadCallLog ReadAccounts"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code for token and return to Analytics tab."""
    # Handle error returns from RingCentral
    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', 'No description provided')
        return f"Authorization Error: {error} - {desc}. <br>Please verify your App Scopes.", 400

    code = request.args.get('code')
    if not code:
        return "Authorization failed: No code returned.", 400

    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    
    # Exchange code for access token using client secret
    response = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    
    if response.ok:
        token_data = response.json()
        # Save to a dedicated analytics token key to keep PKCE session separate
        session['analytics_token'] = token_data.get('access_token')
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
