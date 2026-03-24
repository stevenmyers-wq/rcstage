# webapp/bulk_hours/routes.py
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

bulk_hours_bp = Blueprint(
    'bulk_hours_bp', __name__,
    url_prefix='/api/bulk_hours'
)

@bulk_hours_bp.route('/hours/<entity_type>', methods=['GET'])
@require_rc_token
def get_hours_list(entity_type):
    """Fetches the business hours for 'sites' or 'queues'."""
    if entity_type.lower() not in ['sites', 'queues']:
        return jsonify({"error": "Invalid entity type for hours."}), 400

    formatted_entity_type = "Site" if entity_type.lower() == 'sites' else "Queue"

    try:
        hours_data = utils.fetch_operating_hours(formatted_entity_type)
        return jsonify(hours_data)
    except Exception as e:
        print(f"Error fetching hours: {e}")
        return jsonify({"error": "An internal error occurred while fetching hours."}), 500

@bulk_hours_bp.route('/rules/<entity_type>', methods=['GET'])
@require_rc_token
def get_rules_list(entity_type):
    """Fetches the custom answering rules for 'sites' or 'queues'."""
    if entity_type.lower() not in ['sites', 'queues']:
        return jsonify({"error": "Invalid entity type for rules."}), 400
        
    formatted_entity_type = "Site" if entity_type.lower() == 'sites' else "Queue"

    try:
        rules_data = utils.fetch_custom_rules(formatted_entity_type)
        return jsonify(rules_data)
    except Exception as e:
        print(f"Error fetching rules: {e}")
        return jsonify({"error": "An internal error occurred while fetching rules."}), 500

@bulk_hours_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Bulk Open Hours')
def upload_data():
    """
    Receives either Hours or Rules data and processes the updates.
    The frontend specifies the data type in the payload.
    """
    data = request.get_json()

    if not data or 'records' not in data or 'dataType' not in data:
        return jsonify({"error": "Request body must include 'dataType' and 'records'."}), 400
        
    records = data['records']
    data_type = data['dataType']

    try:
        if data_type == 'Hours':
            results = utils.update_hours_from_records(records)
        elif data_type == 'Rules':
            results = utils.update_rules_from_records(records)
        else:
            return jsonify({"error": f"Invalid dataType '{data_type}' specified."}), 400
            
        return jsonify(results)
    except Exception as e:
        print(f"Error during upload process: {e}")
        return jsonify({"error": "An internal error occurred during the update process."}), 500
