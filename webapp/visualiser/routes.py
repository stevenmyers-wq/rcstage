# webapp/visualiser/routes.py
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    """
    FAILSAFE: Loops through all pages of a RingCentral API endpoint 
    to ensure no data is hidden behind pagination limits.
    """
    if params is None:
        params = {}
    
    # Force max page size to reduce number of API calls
    params['perPage'] = 1000
    params['page'] = 1
    
    all_records = []
    
    while True:
        # Construct URL with current page params
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{endpoint}?{query_string}" if '?' not in endpoint else f"{endpoint}&{query_string}"
        
        response = rc_api_call(full_url)
        
        if not response:
            break
            
        if 'records' in response:
            all_records.extend(response['records'])
        
        # Check if there is a next page in the navigation object
        navigation = response.get('navigation', {})
        next_page = navigation.get('nextPage')
        
        if next_page:
            params['page'] += 1
        else:
            break
            
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    """
    Returns targets for the visualizer.
    If 'query' is present, it filters. If not, it returns ALL targets (for the dropdown).
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0) # If query is empty, return everything
    
    results = []
    
    # 1. Fetch ALL Phone Numbers (Main numbers often hide here)
    # We fetch all pages to ensure we don't miss the main company number if it's record #1001
    phone_records = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
    
    for record in phone_records:
        p_usage = record.get('usageType', '')
        # Filter for relevant main numbers
        if p_usage in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
            p_number = record.get('phoneNumber', '')
            p_ext = record.get('extension')
            
            # If we are searching specific text, check match
            if not return_all and (query not in p_number and query not in p_usage.lower()):
                continue

            if p_ext:
                results.append({
                    'id': p_ext['id'],
                    'text': f"{p_number} ({p_usage})", # Select2 expects 'text'
                    'type': 'PhoneNumber'
                })

    # 2. Fetch ALL Sites
    site_records = fetch_all_pages("/restapi/v1.0/account/~/sites")
    for site in site_records:
        s_name = site.get('name', '')
        if not return_all and query not in s_name.lower():
            continue
            
        results.append({
            'id': site['id'],
            'text': f"Site: {s_name}",
            'type': 'Site'
        })

    # 3. Fetch ALL Extensions (IVRs, Queues, Users)
    # We fetch inactive ones too, just in case a flow involves them.
    # We use fetch_all_pages to ensure we get every single extension.
    ext_records = fetch_all_pages("/restapi/v1.0/account/~/extension", {'status': 'Enabled'})
    
    for ext in ext_records:
        e_name = ext.get('name', '')
        e_number = ext.get('extensionNumber', '')
        e_type = ext.get('type', 'Unknown')
        
        # Filter strictly for Entry Points or requested Search
        if not return_all:
             if query not in e_name.lower() and query != e_number:
                 continue
        
        # Categorize for the dropdown groups
        if e_type in ['IvrMenu', 'CallQueue', 'Department', 'Site', 'User', 'ApplicationExtension']:
            # Icons help distinguish types in the dropdown
            icon = "👤" if e_type == 'User' else "🏢"
            if e_type == 'IvrMenu': icon = "🎹"
            if e_type == 'CallQueue': icon = "👥"
            
            results.append({
                'id': ext['id'],
                'text': f"{icon} {e_name} (Ext: {e_number})",
                'type': e_type,
                'category': e_type 
            })
    
    # Deduplicate by ID (in case an extension appeared in multiple lists)
    final_results = []
    seen_ids = set()
    for item in results:
        if item['id'] not in seen_ids:
            final_results.append(item)
            seen_ids.add(item['id'])
    
    # Sort: IVRs and Queues first, then Users for better UX
    def sort_key(x):
        priority = {'Site': 0, 'IvrMenu': 1, 'CallQueue': 2, 'Department': 3, 'User': 4}
        return priority.get(x['type'], 5)
        
    final_results.sort(key=sort_key)

    return jsonify({'status': 'success', 'results': final_results})

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    """
    Generates the Mermaid.js graph definition for a given starting extension ID.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    # Initialize API log in session
    session['api_log'] = []
    
    try:
        # Generate the mermaid graph
        mermaid_graph_string = generate_mermaid_flow(ext_id)
        
        # Get the API log data
        api_log_data = session.pop('api_log', [])
        
        return jsonify({
            'status': 'success',
            'mermaid_graph': mermaid_graph_string,
            'api_log': api_log_data
        })
    
    except Exception as e:
        api_log_data = session.pop('api_log', [])
        return jsonify({
            'status': 'error',
            'message': f'Error generating call flow: {str(e)}',
            'api_log': api_log_data
        }), 500
