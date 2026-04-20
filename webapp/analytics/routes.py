from flask import Blueprint, request, jsonify
from webapp.analytics.utils import RCBusinessAnalytics
import logging

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No payload provided."}), 400

        # Mapping UI labels to strict API dimension strings
        # 'Extension' is the key for Users; 'Account' is for Entire Company.
        dim_map = {
            'Users': 'Extension',
            'Queues': 'CallQueue',
            'IVRs': 'IvrMenu',
            'Company': 'Account'
        }
        
        ui_dimension = data.get('dimension', 'Users')
        api_dimension = dim_map.get(ui_dimension, 'Extension')
        
        time_from = data.get('timeFrom')
        time_to = data.get('timeTo')
        time_zone = data.get('timeZone', 'UTC')

        rc_analytics = RCBusinessAnalytics()

        time_settings = {
            "timeZone": time_zone,
            "timeRange": {
                "timeFrom": time_from,
                "timeTo": time_to
            }
        }

        result = rc_analytics.fetch_records(
            dimension=api_dimension,
            time_settings=time_settings,
            per_page=100 
        )
        
        return jsonify(result if result else {"data": []})

    except Exception as e:
        logging.error(f"Analytics Route Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
