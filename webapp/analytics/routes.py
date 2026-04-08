import os
from flask import Blueprint, request, jsonify, session
from webapp.analytics.utils import RCBusinessAnalytics

# GCP Environment Variables (SM_CLIENT_ID used for overall app identity)
SM_CLIENT_ID = os.environ.get('SM_CLIENT_ID')
SM_CLIENT_SECRET = os.environ.get('SM_CLIENT_SECRET')

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/validate', methods=['POST'])
def validate_account():
    """Checks if a Target Account ID is provided and accessible via the current session."""
    data = request.json
    target_id = data.get('targetAccountId')

    # 1. Ensure PKCE token exists in session (Matching your rc_api.py key)
    if not session.get('rc_access_token'):
        return jsonify({
            "error": "NOT_CONNECTED", 
            "message": "Your RingCentral account is not connected. Please go to PKCE Setup to login."
        }), 401

    if not target_id or target_id == "~":
        return jsonify({"error": "VALIDATION_ERROR", "message": "A specific Target Account ID is required."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_id)
    
    try:
        # Perform a minimal aggregation call to verify the admin has access to this target ID
        result = rc_analytics.fetch_aggregation(
            grouping={"groupBy": "Company"},
            time_settings={"timeZone": "UTC", "timeRange": {"timeFrom": "2026-01-01T00:00:00Z", "timeTo": "2026-01-01T01:00:00Z"}},
            response_options={"counters": {"allCalls": {"aggregationType": "Sum"}}},
            params={"perPage": 1}
        )
        
        # If rc_api_call returned None (no token) or an error dictionary
        if result is None or (isinstance(result, dict) and 'error' in result):
            error_text = result.get('error') if result else "Unable to reach API"
            return jsonify({"error": "ACCESS_DENIED", "message": f"Validation Failed: {error_text}"}), 403

        # Success: Store this ID as the active context for the session
        session['active_analytics_id'] = target_id
        return jsonify({"success": True, "account": target_id})
        
    except Exception as e:
        return jsonify({"error": "SERVER_ERROR", "message": str(e)}), 500

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """Fetches the actual granular logs for the validated account."""
    data = request.json
    target_account = data.get('targetAccountId') or session.get('active_analytics_id')
    
    if not target_account:
        return jsonify({"error": "NO_CONTEXT", "message": "No target account specified."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_account)

    try:
        result = rc_analytics.fetch_records(
            dimension=data.get('dimension', 'Queues'),
            time_settings={
                "timeZone": data.get('timeZone', 'UTC'),
                "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
            },
            params={"perPage": 100}
        )
        
        if result is None:
            return jsonify({"error": "DISCONNECTED", "message": "RingCentral API session is invalid."}), 500
            
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "FETCH_ERROR", "message": str(e)}), 500
