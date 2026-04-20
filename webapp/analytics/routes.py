from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics

# Create a blueprint for the analytics module
analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """API endpoint called by the frontend JS to fetch granular Call Records."""
    data = request.json
    if not data:
        return jsonify({"error": "No payload provided."}), 400

    time_from = data.get('timeFrom')
    time_to = data.get('timeTo')
    dimension = data.get('dimension', 'Queues') # Mapped from groupBy in UI
    time_zone = data.get('timeZone', 'UTC')

    # Initialize our API Wrapper
    rc_analytics = RCBusinessAnalytics()

    # Construct the TimeSettings based on OpenAPI 3.0.3 spec
    time_settings = {
        "timeZone": time_zone,
        "timeRange": {
            "timeFrom": time_from,
            "timeTo": time_to
        }
    }

    try:
        # Fetch detailed call records
        # Defaulting to 100 records per page (the max allowed by the API)
        result = rc_analytics.fetch_records(
            dimension=dimension,
            time_settings=time_settings,
            page=1,
            per_page=100 
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
