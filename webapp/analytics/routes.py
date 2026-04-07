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
        ui_dimension = data.get('dimension', 'Company')
        time_zone = data.get('timeZone', 'UTC')

        # STRICT API MAPPING:
        # These are the only strings the Records API accepts for these categories.
        dimension_map = {
            'Company': 'Account',
            'Users': 'Extension',
            'Queues': 'CallQueue'
        }
        
        api_dimension = dimension_map.get(ui_dimension, 'Account')

        rc_analytics = RCBusinessAnalytics()
        time_settings = {
            "timeZone": time_zone,
            "timeRange": {
                "timeFrom": time_from,
                "timeTo": time_to
            }
        }

        # Request a broad page of 250 records for stitching
        result = rc_analytics.fetch_records(
            dimension=api_dimension, 
            time_settings=time_settings,
            per_page=250 
        )

        # Safety check: Ensure we always return a JSON object with a data list
        if not isinstance(result, dict) or 'data' not in result:
            result = {"data": []}

        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics Route Error: {str(e)}")
        return jsonify({"error": str(e), "data": []}), 500
