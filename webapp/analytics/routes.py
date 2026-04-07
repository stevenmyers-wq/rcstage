from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

# Create a blueprint for the analytics module
analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """
    API endpoint to fetch broad Call Records.
    We use dimension='Company' to ensure we get all session legs for stitching.
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        time_zone = data.get('timeZone', 'UTC')

        if not time_from or not time_to:
            return jsonify({"error": "timeFrom and timeTo are required"}), 400

        rc_analytics = RCBusinessAnalytics()

        time_settings = {
            "timeZone": time_zone,
            "timeRange": {
                "timeFrom": time_from,
                "timeTo": time_to
            }
        }

        # We fetch for 'Company' dimension to ensure we get every session leg 
        # in the account, allowing the frontend to stitch transfers together.
        result = rc_analytics.fetch_records(
            dimension="Company", 
            time_settings=time_settings,
            page=1,
            per_page=250 
        )

        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics API Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
