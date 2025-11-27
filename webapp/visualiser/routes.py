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
    Standard, reliable paginator.
    """
    if params is None: params = {}
    
    # 1. Set Safe Defaults
    current_params = params.copy()
    current_params['perPage'] = 1000 
    current_params['page'] = 1
    
    all_records = []
    
    print(f"[INFO] Fetching: {endpoint}", file=sys.stdout)
    
    while True:
        try:
            # Construct URL
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            response = rc_api_call(url)
            
            # Error Handling
            if not response:
                print(f"[WARN] Fetch returned None for page {current_params['page']}", file=sys.stderr)
                break
                
            if 'records' in response:
                all_records.extend(response['records'])
            
            # Pagination Check
            nav = response.get('navigation', {})
            if nav.get('nextPage'):
                current_params['page'] += 1
                # Tiny sleep to prevent 429 errors
                time.sleep(0.05)
            else:
                break
                
        except Exception as e:
            print(f"[ERROR] Pagination Exception: {e}", file=sys.stderr)
            break
            
    print(f"[INFO] Fetch Complete. Count: {len(all_records)}", file=sys.stdout)
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    results = []
    
    try:
        # 1. Phones
        # We fetch ALL numbers to ensure we don't miss any
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        
        for p in phones:
            # Loose usage filtering
            if p.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber', 'NumberPool']:
                num = p.get('phoneNumber', '')
                
                # Search Filter
                if not return_all and query not in num: continue
                
                if p.get('extension', {}).get('id'):
                    results.append({
                        'id': p['extension']['id'],
                        'text': f"{num} ({p.get('usageType')})",
                        'type': 'PhoneNumber'
                    })
        
        # 2. Extensions
        # Fetch Enabled AND Disabled. 
        # Note: We intentionally do not filter by type in the API call to ensure we get everything.
        ext_params = {'status': 'Enabled,Disabled'} 
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension", ext_params)
        
        # Broad list of allowed types
        ALLOWED_TYPES = [
            'IvrMenu', 'CallQueue', 'Department', 'Site', 'ApplicationExtension', 
            'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited', 
            'Bot', 'Room', 'ParkLocation', 'SharedLinesGroup'
        ]
        
        for e in exts:
            ename = e.get('name', 'Unknown')
            enum = e.get('extensionNumber', '')
            etype = e.get('type', 'Unknown')
            
            # Search Filter
            if not return_all:
                if query not in ename.lower() and query != enum: continue
            
            if etype in ALLOWED_TYPES:
                status_mk = "" if e.get('status') == 'Enabled' else " [Disabled]"
                results.append({
                    'id': e['id'],
                    'text': f"[{etype}] {ename} (Ext: {enum}){status_mk}",
                    'type': etype
                })

        # Deduplicate
        final_results = []
        seen = set()
        for r in results:
            if r['id'] not in seen:
                final_results.append(r)
                seen.add(r['id'])
        
        # Sort: Queues & IVRs first
        def sort_key(x):
            order = {'Site':0, 'CallQueue':1, 'IvrMenu':2}
            return order.get(x['type'], 99)
            
        final_results.sort(key=sort_key)

        # DIAGNOSTIC: If empty, tell the user why
        if not final_results:
            return jsonify({
                'status': 'success', 
                'results': [{'id': 'err', 'text': '⚠️ No Extensions Found (Check API Logs)', 'disabled': True}]
            })

        return jsonify({'status': 'success', 'results': final_results})

    except Exception as e:
        print(f"[CRITICAL SEARCH ERROR] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401
    
    try:
        # Generate Graph
        graph, logs = generate_mermaid_flow(ext_id)
        
        return jsonify({
            'status': 'success',
            'mermaid_graph': graph,
            'api_log': logs
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': []}), 500
