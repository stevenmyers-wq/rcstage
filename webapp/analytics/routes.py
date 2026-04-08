import os
import requests
import logging
from flask import Blueprint, request, jsonify, session, redirect, url_for

# Configure logging to see errors in GCP Logs
logger = logging.getLogger(__name__)

# GCP Env variables
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

# Must match RC Developer Console exactly
REDIRECT_URI = "https://rcau-api-tools-396158962307.us-central1.run.app/api/analytics/callback"

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/auth')
def analytics_authorize():
    """Step 1: Save target and redirect to RingCentral."""
    target_id = request.args.get('targetAccountId')
    if not target_id:
        return "Missing Target Account ID", 400

    session['analytics_target_id'] = target_id
    
    # Correct scope string for Business Analytics
    scopes = "Analytics ReadCallLog ReadAccounts"
    
    rc_url = (
        f"https://platform.ringcentral.com/restapi/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scopes}"
    )
    return redirect(rc_url)

@analytics_bp.route('/api/analytics/callback')
def analytics_callback():
    """Step 2: Exchange code and return to frontend."""
    error = request.args.get('error')
    if error:
        return f"Auth Error: {error} - {request.args.get('error_description')}", 400

    code = request.args.get('code')
    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    
    try:
        response = requests.post(token_url, data=data, auth=(CLIENT_ID, CLIENT_SECRET))
        if response.ok:
            session['analytics_token'] = response.json().get('access_token')
            # Use an absolute redirect to ensure the user lands back on the app
            return redirect("https://rcau-api-tools-396158962307.us-central1.run.app/#business-analytics")
        return f"Token Exchange Failed: {response.text}", 400
    except Exception as e:
        return f"Callback System Error: {str(e)}", 500

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Step 3: Secure JSON-only endpoint."""
    try:
        data = request.json
        token = session.get('analytics_token')
        target_id = session.get('analytics_target_id')
        
        if not token or not target_id:
            return jsonify({"error": "AUTH_REQUIRED", "message": "Session expired or not authorized."}), 401

        from webapp.analytics.utils import RCBusinessAnalytics
        rc_analytics = RCBusinessAnalytics(account_id=target_id, token=token)

        result = rc_analytics.fetch_records(
            dimension=data.get('dimension', 'Queues'),
            time_settings={
                "timeZone": data.get('timeZone', 'UTC'),
                "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
            }
        )
        
        if result is None:
            return jsonify({"error": "EMPTY_RESPONSE", "message": "RingCentral returned no data."}), 500
            
        return jsonify(result)

    except Exception as e:
        logger.error(f"Analytics Route Error: {str(e)}")
        # We catch everything and return JSON so the UI doesn't see '<'
        return jsonify({"error": "SERVER_ERROR", "message": str(e)}), 500
