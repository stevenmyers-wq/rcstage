from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    """
    API endpoint to fetch Call Records.
    Maps UI labels to strict RingCentral Analytics Dimension keys.
    """
    try:
        data = request.json or {}
        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        ui_dimension = data.get('dimension', 'Users')
        time_zone = data.get('timeZone', 'UTC')

        # CRITICAL: Mapping UI labels to strict API Dimension keys
        # The API will return 0 results if these strings aren't exact.
        dimension_map = {
            'Company': 'Account',
            'Users': 'Extension',
            'Queues': 'CallQueue',
            'IVRs': 'IvrMenu',
            'Sites': 'Site'
        }
        
        api_dimension = dimension_map.get(ui_dimension, 'Extension')

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

        # Fetching records using the mapped dimension key
        result = rc_analytics.fetch_records(
            dimension=api_dimension, 
            time_settings=time_settings,
            page=1,
            per_page=250 
        )

        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics API Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
