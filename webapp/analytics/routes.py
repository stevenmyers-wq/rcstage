import os
from flask import Blueprint, request, jsonify, session
from webapp.analytics.utils import RCBusinessAnalytics

# Pulling from GCP Environment Variables
SM_CLIENT_ID = os.environ.get('SM_CLIENT_ID')
SM_CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/validate', methods=['POST'])
def validate_account():
    """Validates the Account ID by attempting a minimal metadata call."""
    data = request.json
    target_id = data.get('targetAccountId')

    if not target_id:
        return jsonify({"error": "Target Account ID is required."}), 400

    # Initialize client to test access
    rc_analytics = RCBusinessAnalytics(account_id=target_id)
    
    try:
        # Minimal 'ping' call to verify the ID exists and we have permissions
        rc_analytics.fetch_aggregation(
            grouping={"groupBy": "Company"},
            time_settings={"timeZone": "UTC", "timeRange": {"timeFrom": "2024-01-01T00:00:00Z", "timeTo": "2024-01-01T01:00:00Z"}},
            response_options={"counters": {"allCalls": {"aggregationType": "Sum"}}},
            per_page=1
        )
        session['active_analytics_id'] = target_id
        return jsonify({"success": True, "account": target_id})
    except Exception as e:
        return jsonify({"error": f"Validation Failed: {str(e)}"}), 401

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    data = request.json
    target_account = data.get('targetAccountId') or session.get('active_analytics_id')
    
    if not target_account:
        return jsonify({"error": "No active account session found."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_account)

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
