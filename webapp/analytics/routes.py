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

        # STRICT SINGULAR MAPPING: The Records API fails on plural strings.
        dimension_map = {
            'Company': 'Account',
            'Users': 'User',
            'Queues': 'Queue',
            'IVRs': 'IVR'
        }
        
        api_dimension = dimension_map.get(ui_dimension, 'User')

        rc_analytics = RCBusinessAnalytics()
        time_settings = {
            "timeZone": time_zone,
            "timeRange": {"timeFrom": time_from, "timeTo": time_to}
        }

        # We pull 250 records per page to ensure we have enough 'legs' 
        # to stitch transfers together in the frontend.
        result = rc_analytics.fetch_records(
            dimension=api_dimension, 
            time_settings=time_settings,
            per_page=250 
        )

        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics API Route Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
