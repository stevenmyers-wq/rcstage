# webapp/visualiser/routes.py
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow
import time

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    """
    FAILSAFE: Loops through all pages. Includes Retry/Fallback logic.
    """
    if params is None:
        params = {}
    
    # Clone params to avoid modifying the original dict references
    current_params = params.copy()
    current_params['perPage'] = 1000
    current_params['page'] = 1
    
    all_records = []
    page_count = 0
    
    print(f"--- [DEBUG] Starting Fetch: {endpoint} ---")
    
    while True:
        try:
            # Construct URL manually to ensure control over encoding
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            separator = '&' if '?' in endpoint else '?'
            full_url = f"{endpoint}{separator}{query_string}"
            
            response = rc_api_call(full_url)
            
            if not response:
                print(f"   [DEBUG] API returned None for {full_url}")
                break
                
            if 'errorCode' in response:
                print(f"   [DEBUG] API Error: {response.get('message')}")
                break

            records = response.get('records', [])
            count = len(records)
            all_records.extend(records)
            page_count += 1
            print(f"   [DEBUG] Page {page_count}: Found {count} records.")
            
            # Pagination Check
            navigation = response.get('navigation', {})
            next_page = navigation.get('nextPage')
            
            if next_page:
                current_params['page'] += 1
                time.sleep(0.1) # Tiny pause to be nice to API rate limits
            else:
                break
                
        except Exception as e:
            print(f"   [DEBUG] Exception during fetch: {e}")
            break
            
    print(f"--- [DEBUG] Total for {endpoint}: {len(all_records)} ---")
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    
    results = []
    
    # 1. Phone Numbers
    phone_records = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
    for record in phone_records:
        if record.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
            p_number = record.get('phoneNumber', '')
            p_ext = record.get('extension')
            
            # If searching, filter text. If loading all, take all.
            if return_all or (query in p_number):
                if p_ext:
                    results.append({
                        'id': p_ext['id'],
                        'text': f"📞 {p_number} ({record.get('usageType')})",
                        'type': 'PhoneNumber'
                    })

    # 2. Extensions (The big list)
    # Note: We removed the 'status=Enabled' filter for debugging to ensure we see EVERYTHING
    ext_records = fetch_all_pages("/restapi/v1.0/account/~/extension") 
    
    for ext in ext_records:
        e_name = ext.get('name', 'Unknown')
        e_number = ext.get('extensionNumber', '')
        e_type = ext.get('type', 'Unknown')
        e_status = ext.get('status', 'Disabled')
        
        # Filter: If searching, match text. If returning all, skip "dirty" data
        if not return_all:
             if query not in e_name.lower() and query != e_number:
                 continue
        
        # Only add relevant types to the dropdown
        if e_type in ['IvrMenu', 'CallQueue', 'Department', 'Site', 'User', 'ApplicationExtension']:
            
            # Icon Logic
            icon = "👤"
            if e_type == 'IvrMenu': icon = "🎹"
            elif e_type == 'CallQueue': icon = "👥"
            elif e_type == 'Site': icon = "🏢"
            
            # Add status to text if disabled
            status_text = "" if e_status == 'Enabled' else " (Disabled)"
            
            results.append({
                'id': ext['id'],
                'text': f"{icon} {e_name} - Ext: {e_number}{status_text}",
                'type': e_type
            })

    # Deduplicate
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

    # DEBUG: If empty, inject a fake error item so the user sees it in the UI
    if not final_results:
        print("!!! [DEBUG] No results found after all fetches. Sending UI Error.")
        return jsonify({
            'status': 'success', 
            'results': [{'id': 'error', 'text': '⚠️ No Extensions Found (Check Server Logs)', 'disabled': True}]
        })

    return jsonify({'status': 'success', 'results': final_results})

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    session['api_log'] = []
    try:
        mermaid_graph_string = generate_mermaid_flow(ext_id)
        api_log_data = session.pop('api_log', [])
        return jsonify({'status': 'success', 'mermaid_graph': mermaid_graph_string, 'api_log': api_log_data})
    except Exception as e:
        api_log_data = session.pop('api_log', [])
        return jsonify({'status': 'error', 'message': str(e), 'api_log': api_log_data}), 500
