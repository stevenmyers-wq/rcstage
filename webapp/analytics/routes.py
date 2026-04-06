from flask import Blueprint, render_template, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics

# Create a blueprint for the analytics module
analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/analytics')
def analytics_dashboard():
    """Renders the main Analytics HTML page."""
    return render_template('analytics.html')

@analytics_bp.route('/api/analytics/aggregation', methods=['POST'])
def get_aggregation_report():
    """API endpoint called by the frontend JS to fetch RC Analytics."""
    data = request.json
    if not data:
        return jsonify({"error": "No payload provided."}), 400

    time_from = data.get('timeFrom')
    time_to = data.get('timeTo')
    group_by = data.get('groupBy', 'Queues')
    time_zone = data.get('timeZone', 'UTC')

    # Initialize our API Wrapper
    rc_analytics = RCBusinessAnalytics()

    # Construct the Analytics Payload based on OpenAPI 3.0.3 spec
    grouping = {
        "groupBy": group_by
    }
    
    time_settings = {
        "timeZone": time_zone,
        "timeRange": {
            "timeFrom": time_from,
            "timeTo": time_to
        }
    }
    
    # Pull Total Calls and the exact breakdown of Call Results
    response_options = {
        "counters": {
            "allCalls": {"aggregationType": "Sum"},
            "callsByResult": {"aggregationType": "Sum"}
        },
        "timers": {
            "allCallsDuration": {"aggregationType": "Average"}
        }
    }

    try:
        result = rc_analytics.fetch_aggregation(
            grouping=grouping,
            time_settings=time_settings,
            response_options=response_options
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
