from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

# Create a blueprint for the analytics module
analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """
    API endpoint to fetch Call Records.
    Restored dimension selection to ensure data returns based on user permissions.
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        # Extract parameters with defaults
        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        dimension = data.get('dimension', 'Company')
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

        # Fetch up to 250 records to allow for forensic stitching of transfers
        result = rc_analytics.fetch_records(
            dimension=dimension, 
            time_settings=time_settings,
            page=1,
            per_page=250 
        )

        # Log for debugging
        if result and 'data' in result:
            logging.info(f"Analytics API returned {len(result['data'])} sessions.")
        
        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics API Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
