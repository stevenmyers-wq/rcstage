import os
import requests
from flask import Blueprint, request, jsonify, session, redirect, current_app
from webapp.analytics.utils import RCBusinessAnalytics

# GCP Env variables
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Save the Target ID and Redirect to RingCentral."""
    target_id = request.args.get('targetAccountId')
    if not target_id:
        return "Missing Target Account ID", 400

    # Save target ID in session so it's there when we return from RC
    session['analytics_target_id'] = target_id
    
    # Construct Redirect URI (Ensure this is in your RC App settings)
    # The URL needs to match your Cloud Run environment
    redirect_uri = "https://rcau-api-tools-396158962307.us-central1.run.app/api/analytics/callback"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={redirect_uri}&scope=ReadAnalytics"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code for token and return to Analytics tab."""
    code = request.args.get('code')
    redirect_uri = "https://rcau-api-tools-396158962307.us-central1.run.app/api/analytics/callback"
    
    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri
    }
    
    # Using Client Secret from GCP for the handshake
    response = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    
    if response.ok:
        token_data = response.json()
        # Save to a SPECIFIC analytics key to avoid breaking global PKCE session
        session['analytics_token'] = token_data.get('access_token')
        # Redirect back to the UI (assuming / is your dashboard root)
        return redirect('/#business-analytics') 
    
    return f"Auth Failed: {response.text}", 400

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Run the query using the analytics-specific token."""
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
