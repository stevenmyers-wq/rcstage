import os
from flask import Blueprint, request, jsonify, session
from webapp.analytics.utils import RCBusinessAnalytics

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/validate', methods=['POST'])
def validate_account():
    """Checks if the provided Account ID is valid before unlocking the UI."""
    data = request.json
    target_id = data.get('targetAccountId')

    if not target_id or target_id == "~":
        return jsonify({"error": "A specific Account ID is required."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_id)
    
    try:
        # We do a tiny metadata call or a 1-record limit call to verify access
        # Using a minimal aggregation call as a 'ping'
        rc_analytics.fetch_aggregation(
            grouping={"groupBy": "Company"},
            time_settings={"timeZone": "UTC", "timeRange": {"timeFrom": "2024-01-01T00:00:00Z", "timeTo": "2024-01-01T01:00:00Z"}},
            response_options={"counters": {"allCalls": {"aggregationType": "Sum"}}},
            per_page=1
        )
        session['active_analytics_id'] = target_id
        return jsonify({"success": True, "message": f"Authenticated for Account {target_id}"})
    except Exception as e:
        return jsonify({"error": f"Authentication failed: {str(e)}"}), 401

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    data = request.json
    # STRICT: No default to '~'
    target_account = data.get('targetAccountId')
    
    if not target_account:
        return jsonify({"error": "No target account specified."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_account)

    try:
        result = rc_analytics.fetch_records(
            dimension=data.get('dimension'),
            time_settings={
                "timeZone": data.get('timeZone', 'UTC'),
                "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
            }
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
