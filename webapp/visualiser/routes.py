# webapp/visualiser/routes.py
import sys
import time
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    """
    Fetches all pages from RC API.
    Includes ERROR LOGGING to prevent silent failures.
    """
    if params is None: params = {}
    current_params = params.copy()
    current_params['perPage'] = 1000
    current_params['page'] = 1
    all_records = []
    
    print(f"[INFO] Starting fetch for: {endpoint}", file=sys.stdout)
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            separator = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{separator}{query_string}"
            
            response = rc_api_call(url)
            
            # Error Check
            if not response:
                print(f"[WARN] API returned None for {url}", file=sys.stderr)
                break
            
            if 'errorCode' in response:
                print(f"[ERROR] API Error {response.get('errorCode')}: {response.get('message')}", file=sys.stderr)
                break

            if 'records' in response:
                all_records.extend(response['records'])
            
            # Pagination Logic
            nav = response.get('navigation', {})
            if nav.get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.1) # Rate limit protection
            else:
                break
                
        except Exception as e:
            print(f"[ERROR] Exception in fetch loop: {str(e)}", file=sys.stderr)
            break
            
    print(f"[INFO] Fetch complete. Total records: {len(all_records)}", file=sys.stdout)
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    """
    Returns targets for the visualizer dropdown.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    
    results = []
    
    try:
        # 1. Phone Numbers (Entry Points)
        phone_records = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        for record in phone_records:
            if record.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
                p_number = record.get('phoneNumber', '')
                
                # Filter if searching
                if not return_all and query not in p_number: continue
                
                # Verify extension object exists before accessing 'id'
                if record.get('extension') and 'id' in record['extension']:
                    results.append({
                        'id': record['extension']['id'],
                        'text': f"{p_number} ({record.get('usageType')})",
                        'type': 'PhoneNumber'
                    })

        # 2. Extensions (Users, Queues, IVRs)
        ext_records = fetch_all_pages("/restapi/v1.0/account/~/extension")
        for ext in ext_records:
            e_name = ext.get('name', 'Unknown')
            e_number = ext.get('extensionNumber', '')
            e_type = ext.get('type', 'Unknown')
            
            # Filter if searching
            if not return_all:
                if query not in e_name.lower() and query != e_number: continue
            
            # Filter by valid types
            if e_type in ['IvrMenu', 'CallQueue', 'Department', 'Site', 'User', 'ApplicationExtension']:
                status = "" if ext.get('status') == 'Enabled' else " [Disabled]"
                
                results.append({
                    'id': ext['id'],
                    'text': f"[{e_type}] {e_name} (Ext: {e_number}){status}",
                    'type': e_type
                })
        
        # Deduplicate results
        final_results = []
        seen = set()
        for item in results:
            if item['id'] not in seen:
                final_results.append(item)
                seen.add(item['id'])
                
        # Sort logic
        final_results.sort(key=lambda x: {'Site':0, 'IvrMenu':1, 'CallQueue':2}.get(x['type'], 5))

        return jsonify({'status': 'success', 'results': final_results})

    except Exception as e:
        print(f"[CRITICAL ERROR] Search route failed: {str(e)}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    session['api_log'] = []
    try:
        # Generate the sophisticated mermaid graph (Logic from utils.py)
        mermaid_graph = generate_mermaid_flow(ext_id)
        
        return jsonify({
            'status': 'success',
            'mermaid_graph': mermaid_graph,
            'api_log': session.pop('api_log', [])
        })
    except Exception as e:
        print(f"[ERROR] Trace failed for {ext_id}: {e}", file=sys.stderr)
        return jsonify({
            'status': 'error',
            'message': str(e),
            'api_log': session.pop('api_log', [])
        }), 500
