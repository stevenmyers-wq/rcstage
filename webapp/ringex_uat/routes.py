from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from . import utils

ringex_uat_bp = Blueprint('ringex_uat_bp', __name__)

@ringex_uat_bp.route('/api/ringex_uat/entities', methods=['GET'])
@require_rc_token
def api_get_entities():
    try:
        entities = utils.get_testable_extensions()
        return jsonify({"success": True, "entities": entities})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@ringex_uat_bp.route('/api/ringex_uat/generate', methods=['POST'])
@require_rc_token
def api_generate_uat():
    data = request.get_json()
    if not data or 'extension_id' not in data:
        return jsonify({"success": False, "error": "Missing extension ID."}), 400
        
    ext_id = data['extension_id']
    ext_name = data.get('extension_name', 'Unknown')
    ext_number = data.get('extension_number', 'Unknown')
    ext_type = data.get('extension_type', 'Unknown')
    
    try:
        cases = utils.generate_uat_cases(ext_id, ext_name, ext_number, ext_type)
        return jsonify({"success": True, "cases": cases})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
