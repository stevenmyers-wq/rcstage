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
    data = request.get_json() or {}

    # Multi-select routing scope: prefer a 'targets' array, fall back to the
    # legacy single-target fields for backward compatibility.
    targets = data.get('targets')
    if not isinstance(targets, list):
        targets = []
        if data.get('extension_id'):
            targets = [{
                "id": data.get('extension_id'),
                "name": data.get('extension_name', 'Unknown'),
                "number": data.get('extension_number', 'Unknown'),
                "type": data.get('extension_type', 'Unknown'),
            }]

    complexity = data.get('complexity', 'complex')
    suites = data.get('suites') or []
    if not isinstance(suites, list):
        suites = []

    # A routing target is optional as long as at least one standalone suite is selected.
    if not targets and not suites:
        return jsonify({"success": False, "error": "Select a routing target or at least one test suite."}), 400

    try:
        cases = utils.generate_uat_cases(targets=targets, complexity=complexity, suites=suites)
        return jsonify({"success": True, "cases": cases})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
