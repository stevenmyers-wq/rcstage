import os
from flask import Blueprint, request, jsonify, session
from webapp.analytics.utils import RCBusinessAnalytics

# GCP Env Variables are available via os.environ
CLIENT_ID = os.environ.get('SM_CLIENT_ID')
CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """API endpoint to fetch granular Call Records with Impersonation support."""
    data = request.json
    if not data:
        return jsonify({"error": "No payload provided."}), 400

    # Impersonation Check: Grab the target ID from session or default to self ("~")
    target_account = session.get('impersonate_id', '~')

    # Initialize the wrapper with the impersonated account ID
    rc_analytics = RCBusinessAnalytics(account_id=target_account)

    time_settings = {
        "timeZone": data.get('timeZone', 'UTC'),
        "timeRange": {
            "timeFrom": data.get('timeFrom'),
            "timeTo": data.get('timeTo')
        }
    }

    try:
        # Fetch detailed call records
        result = rc_analytics.fetch_records(
            dimension=data.get('dimension', 'Queues'),
            time_settings=time_settings,
            page=1,
            per_page=100 
        )
        
        # Add metadata for the frontend to display impersonation state
        result['impersonated'] = (target_account != "~")
        result['active_account'] = target_account
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
