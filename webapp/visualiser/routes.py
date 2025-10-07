from flask import Blueprint, jsonify, request, session
from webapp.utils import (
    is_authenticated, get_rc_access_token, rc_api_call, trace_flow_recursive, 
    extension_cache
)

viz_bp = Blueprint('visualiser', __name__)

@viz_bp.route('/api/rc/phone-numbers', methods=['GET'])
def get_phone_numbers():
    """Fetches the list of phone numbers and extensions that can be visualized."""
    if not is_authenticated():
        return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    if not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'RingCentral not connected.'}), 401

    response_data = rc_api_call("/restapi/v1.0/account/~/phone-number?perPage=1000")
    
    if response_data is None:
        last_log = session.get('api_log', [{}])[-1]
        return jsonify({
            'status': 'error', 
            'message': f"RC API Failed. Code: {last_log.get('code', 'N/A')}. Detail: {last_log.get('detail', 'N/A')}."
        }), 500
    
    if 'records' not in response_data or not isinstance(response_data.get('records'), list):
        return jsonify({'status': 'error', 'message': 'RC API returned invalid data format.'}), 500

    numbers = []
    VALID_USAGE_TYPES = [
        "MainCompanyNumber", "AdditionalCompanyNumber", "CompanyNumber", "DirectNumber", 
        "CompanyFaxNumber", "ForwardedNumber", "ForwardedCompanyNumber"
    ]

    for record in response_data.get('records', []):
        phone_number = record.get('phoneNumber')
        usage_type = record.get('usageType')
        ext_info = record.get('extension')
        
        if usage_type not in VALID_USAGE_TYPES or not phone_number:
            continue
            
        ext_id = ext_info.get('id') if ext_info else record.get('id')
        name = f"Ext: {ext_info.get('extensionNumber')}" if ext_info else usage_type
        
        if ext_id:
            numbers.append({"id": ext_id, "number": phone_number, "usage": usage_type, "name": name})

    if not numbers:
        return jsonify({'status': 'success', 'numbers': [{"id": "mock1", "number": "+61280000000", "usage": "IVR Menu", "name": "No Live Numbers Found (Mock)"}]}), 200

    return jsonify({'status': 'success', 'numbers': numbers}), 200

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    """Generates the raw flow data structure for the HTML renderer."""
    if not is_authenticated():
        return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    if not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'RingCentral not connected.'}), 401

    phone_number_text = request.args.get('phoneNumber', f"ID: {ext_id}")
    
    # Clear the cache and log for a new run
    extension_cache.clear()
    session['api_log'] = [] 
    
    flow_data = [{'id': 'N0', 'type': 'incoming', 'name': 'Incoming Call', 'details': [f"Number: {phone_number_text}"]}]
    processed_extensions = {}
    
    node_counter, final_flow_data = trace_flow_recursive(ext_id, 1, flow_data, processed_extensions)
    api_log_data = session.pop('api_log', [])
    
    return jsonify({'status': 'success', 'flow_data': final_flow_data, 'api_log': api_log_data}), 200