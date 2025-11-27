# webapp/visualiser/routes.py
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow
import time

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    """
    FAILSAFE: Loops through all pages of a RingCentral API endpoint 
    to ensure no data is hidden behind pagination limits.
    """
    if params is None:
        params = {}
    
    # Clone params to avoid modifying the original dict references
    current_params = params.copy()
    current_params['perPage'] = 1000 # Maximize page size
    current_params['page'] = 1
    
    all_records = []
    
    while True:
        try:
            # Construct URL manually to ensure control
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            separator = '&' if '?' in endpoint else '?'
            full_url = f"{endpoint}{separator}{query_string}"
            
            response = rc_api_call(full_url)
            
            if not response:
                break
            
            if 'records' in response:
                all_records.extend(response['records'])
            
            # Check navigation for next page
            navigation = response.get('navigation', {})
            next_page = navigation.get('nextPage')
            
            if next_page:
                current_params['page'] += 1
                # Small sleep to be polite to the API rate limiter
                time.sleep(0.1)
            else:
                break
                
        except Exception as e:
            print(f"Error fetching page: {e}")
            break
            
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    """
    Returns targets for the visualizer.
    If 'query' is present, it filters. If not, it returns ALL targets.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0) # If query is empty, return everything
    
    results = []
    
    # 1. Fetch ALL Phone Numbers
    phone_records = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
    for record in phone_records:
        p_usage = record.get('usageType', '')
        if p_usage in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
            p_number = record.get('phoneNumber', '')
            p_ext = record.get('extension')
            
            # Search Filter
            if not return_all and (query not in p_number and query not in p_usage.lower()):
                continue

            if p_ext:
                results.append({
                    'id': p_ext['id'],
                    'text': f"📞 {p_number} ({p_usage})",
                    'type': 'PhoneNumber'
                })

    # 2. Fetch ALL Extensions (IVRs, Queues, Users, Sites)
    # We fetch everything to ensure we don't miss anything.
    ext_records = fetch_all_pages("/restapi/v1.0/account/~/extension")
    
    for ext in ext_records:
        e_name = ext.get('name', 'Unknown')
        e_number = ext.get('extensionNumber', '')
        e_type = ext.get('type', 'Unknown')
        e_status = ext.get('status', 'Disabled')
        
        # Search Filter
        if not return_all:
             if query not in e_name.lower() and query != e_number:
                 continue
        
        # Categorize: Only add useful entry points
        if e_type in ['IvrMenu', 'CallQueue', 'Department', 'Site', 'User', 'ApplicationExtension']:
            
            # Icons
            icon = "👤"
            if e_type == 'IvrMenu': icon = "🎹"
            elif e_type == 'CallQueue': icon = "👥"
            elif e_type == 'Site': icon = "🏢"
            
            # Formatting
            status_text = "" if e_status == 'Enabled' else " (Disabled)"
            
            results.append({
                'id': ext['id'],
                'text': f"{icon} {e_name} (Ext: {e_number}){status_text}",
                'type': e_type
            })
    
    # Deduplicate by ID
    final_results = []
    seen_ids = set()
    for item in results:
        if item['id'] not in seen_ids:
            final_results.append(item)
            seen_ids.add(item['id'])
    
    # Sort
    def sort_key(x):
        priority = {'Site': 0, 'IvrMenu': 1, 'CallQueue': 2, 'User': 4}
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
    
    session['api_log'] = []
    
    try:
        mermaid_graph_string = generate_mermaid_flow(ext_id)
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
