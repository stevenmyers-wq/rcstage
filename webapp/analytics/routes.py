from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

# Create a blueprint for the analytics module
analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """API endpoint to fetch Broad Call Records for stitching."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        # Extract parameters with defaults to prevent crashes
        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        time_zone = data.get('timeZone', 'UTC')

        if not time_from or not time_to:
            return jsonify({"error": "timeFrom and timeTo are required"}), 400

        # Initialize API Wrapper
        rc_analytics = RCBusinessAnalytics()

        time_settings = {
            "timeZone": time_zone,
            "timeRange": {
                "timeFrom": time_from,
                "timeTo": time_to
            }
        }

        # IMPORTANT: We use dimension="Company" here to ensure we get 
        # all legs of a call for the account, allowing the frontend 
        # to stitch transfers together.
        result = rc_analytics.fetch_records(
            dimension="Company", 
            time_settings=time_settings,
            page=1,
            per_page=250 
        )

        return jsonify(result)

    except Exception as e:
        # Log the error so you can see it in your GCP logs
        logging.error(f"Analytics API Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
