from flask import Blueprint, request, jsonify
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
# Import the helper function
from webapp.extension_renamer.utils import prepare_extension_for_update

renamer_bp = Blueprint('renamer_bp', __name__)

@renamer_bp.route('/api/renamer/list-extensions', methods=['GET'])
@require_rc_token
def list_extensions():
    all_extensions = []
    page = 1
    total_pages = 1
    
    try:
        while page <= total_pages:
            # Fetch 'account' level extensions to see Users, IVRs, Queues, Sites, etc.
            response = rc_api_call(f"/restapi/v1.0/account/~/extension?page={page}&perPage=1000")
            
            if not response:
                break
                
            all_extensions.extend(response.get('records', []))
            total_pages = response.get('paging', {}).get('totalPages', 1)
            page += 1

        return jsonify(all_extensions)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@renamer_bp.route('/api/renamer/update-extension', methods=['POST'])
@require_rc_token
def update_extension():
    data = request.get_json()
    ext_id = data.get('id')
    ext_type = data.get('type')
    
    # Get separate fields (default to empty string if missing)
    first_name = data.get('firstName', '')
    last_name = data.get('lastName', '')

    if not ext_id:
        return jsonify({"error": "Missing ID"}), 400

    try:
        # 1. Fetch current data
        current_data = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
        
        if not current_data:
            return jsonify({"error": "Extension not found"}), 404

        # 2. Use our UTILS function to prepare the data
        final_data = prepare_extension_for_update(current_data, first_name, last_name, ext_type)

        # 3. Perform Update via PUT
        update_response = rc_api_call(
            f"/restapi/v1.0/account/~/extension/{ext_id}",
            method="PUT",
            json=final_data
        )

        return jsonify({"success": True, "id": ext_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
