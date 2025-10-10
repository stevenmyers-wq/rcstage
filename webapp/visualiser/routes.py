# webapp/visualiser/routes.py
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    """
    Searches for phone numbers, sites, and extensions based on a query string.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401

    query = request.args.get('query', '').lower().strip()
    if len(query) < 3:
        # Don't search if the query is too short
        return jsonify({'status': 'success', 'results': []})

    results = []
    
    # API calls to fetch all possible targets
    phone_data = rc_api_call("/restapi/v1.0/account/~/phone-number?perPage=1000")
    sites_data = rc_api_call("/restapi/v1.0/account/~/sites?perPage=1000")
    ext_data = rc_api_call("/restapi/v1.0/account/~/extension?perPage=1000")

    # 1. Process Phone Numbers
    if phone_data and phone_data.get('records'):
        for record in phone_data['records']:
            p_number = record.get('phoneNumber', '')
            p_usage = record.get('usageType', '')
            p_ext = record.get('extension')
            if p_ext and (query in p_number or query in p_usage.lower()):
                results.append({
                    'id': p_ext['id'],
                    'name': f"{p_number} ({p_usage})",
                    'type': 'PhoneNumber'
                })

    # 2. Process Sites
    if sites_data and sites_data.get('records'):
        for site in sites_data['records']:
            s_name = site.get('name', '')
            if query in s_name.lower():
                results.append({
                    'id': site['id'],
                    'name': s_name,
                    'type': 'Site'
                })

    # 3. Process Extensions (Users, IVRs, Queues)
    if ext_data and ext_data.get('records'):
        for ext in ext_data['records']:
            e_name = ext.get('name', '')
            e_number = ext.get('extensionNumber', '')
            e_type = ext.get('type', 'Unknown')
            if query in e_name.lower() or query == e_number:
                if e_type in ['User', 'IvrMenu', 'CallQueue', 'Department', 'Site']:
                     results.append({
                        'id': ext['id'],
                        'name': f"{e_name} (Ext: {e_number})",
                        'type': e_type
                    })

    # Remove duplicates by ID, keeping the first entry found
    final_results = []
    seen_ids = set()
    for item in results:
        if item['id'] not in seen_ids:
            final_results.append(item)
            seen_ids.add(item['id'])
            
    # Return up to 20 matching results
    return jsonify({'status': 'success', 'results': final_results[:20]})

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    """
    Generates the Mermaid.js graph definition for a given starting extension ID.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401

    session['api_log'] = []
    mermaid_graph_string = generate_mermaid_flow(ext_id)
    api_log_data = session.pop('api_log', [])
    
    return jsonify({
        'status': 'success',
        'mermaid_graph': mermaid_graph_string,
        'api_log': api_log_data
    })


