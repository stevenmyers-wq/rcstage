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

        # Precise mapping for the Records API
        dimension_map = {
            'Users': 'Extension',
            'Queues': 'CallQueue',
            'Company': 'Account'
        }
        
        api_dimension = dimension_map.get(ui_dimension, 'Extension')

        rc_analytics = RCBusinessAnalytics()
        time_settings = {
            "timeZone": time_zone,
            "timeRange": {"timeFrom": time_from, "timeTo": time_to}
        }

        result = rc_analytics.fetch_records(
            dimension=api_dimension, 
            time_settings=time_settings,
            per_page=100 
        )

        return jsonify(result if result else {"data": []})

    except Exception as e:
        logging.error(f"Analytics API Error: {str(e)}")
        return jsonify({"error": str(e), "data": []}), 500
