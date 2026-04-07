from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    try:
        data = request.json or {}
        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        ui_dimension = data.get('dimension', 'Users')
        time_zone = data.get('timeZone', 'UTC')

        # STRICT API MAPPING:
        # Records API requires these exact strings. 'Extension' covers Users.
        dimension_map = {
            'Users': 'Extension',
            'Queues': 'CallQueue',
            'EntireAccount': 'Account'
        }
        
        api_dimension = dimension_map.get(ui_dimension, 'Extension')

        rc_analytics = RCBusinessAnalytics()
        time_settings = {
            "timeZone": time_zone,
            "timeRange": {
                "timeFrom": time_from,
                "timeTo": time_to
            }
        }

        # per_page=100 is the most stable limit across all account tiers
        result = rc_analytics.fetch_records(
            dimension=api_dimension, 
            time_settings=time_settings,
            per_page=100 
        )

        if not result or 'data' not in result:
            result = {"data": []}

        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics Route Error: {str(e)}")
        return jsonify({"error": str(e), "data": []}), 500
