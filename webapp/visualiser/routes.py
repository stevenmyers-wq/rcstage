# webapp/visualiser/routes.py
import sys
import time
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages_robust(endpoint, params=None):
    """
    Robust fetcher that handles pagination with retries.
    Ensures we don't silently lose data if one page fails.
    """
    if params is None: params = {}
    
    # Force high limit and explicit status
    # Requesting ALL statuses ensures we don't miss 'NotActivated' queues or 'Disabled' users
    params['perPage'] = 1000 
    params['page'] = 1
    if 'status' not in params:
        params['status'] = 'Enabled,Disabled,NotActivated'
        
    all_records = []
    
    print(f"[INFO] Starting Robust Fetch: {endpoint}", file=sys.stdout)
    
    while True:
        success = False
        retry_count = 0
        
        # Retry loop for the CURRENT page
        while retry_count < 3 and not success:
            try:
                query_string = "&".join([f"{k}={v}" for k, v in params.items()])
                sep = '&' if '?' in endpoint else '?'
                url = f"{endpoint}{sep}{query_string}"
                
                resp = rc_api_call(url)
                
                if resp and 'records' in resp:
                    all_records.extend(resp['records'])
                    success = True
                else:
                    # If API returns garbage/None, wait and retry
                    print(f"[WARN] Page {params['page']} failed (Attempt {retry_count+1}). Retrying...", file=sys.stderr)
                    retry_count += 1
                    time.sleep(0.5)
                    
            except Exception as e:
                print(f"[ERROR] Page {params['page']} Exception: {e}", file=sys.stderr)
                retry_count += 1
                time.sleep(0.5)
        
        if not success:
            print(f"[CRITICAL] Failed to fetch Page {params['page']} after 3 attempts. Stopping fetch.", file=sys.stderr)
            break
            
        # Check for Next Page
        try:
            nav = resp.get('navigation', {})
            if nav.get('nextPage'):
                params['page'] += 1
                time.sleep(0.1) # Gentle rate limit
            else:
                break
        except:
            break
            
    print(f"[INFO] Fetch Complete. Total Records: {len(all_records)}", file=sys.stdout)
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    results = []
    
    try:
        # 1. Fetch Phones
        phones = fetch_all_pages_robust("/restapi/v1.0/account/~/phone-number")
        for p in phones:
            if p.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
                num = p.get('phoneNumber', '')
                # Simple filter
                if not return_all and query not in num: continue
                
                if p.get('extension', {}).get('id'):
                    results.append({
                        'id': p['extension']['id'],
                        'text': f"{num} ({p.get('usageType')})",
                        'type': 'PhoneNumber'
                    })
        
        # 2. Fetch Extensions (The Critical Part)
        # We deliberately don't filter by type in the API call, we filter in Python
        exts = fetch_all_pages_robust("/restapi/v1.0/account/~/extension")
        
        # EXPANDED Type List - ensuring we don't miss exotic queue types
        ALLOWED_TYPES = [
            'User', 'DigitalUser', 'VirtualUser', 'FaxUser', 'FlexibleUser', 'Limited',
            'CallQueue', 'Department', 'IvrMenu', 'ApplicationExtension', 
            'Site', 'ParkLocation', 'SharedLinesGroup', 'Bot', 'Room'
        ]
        
        for e in exts:
            ename = e.get('name', 'Unknown')
            enum = e.get('extensionNumber', '')
            etype = e.get('type', 'Unknown')
            
            # Search Filter
            if not return_all:
                if query not in ename.lower() and query != enum: continue
            
            # Type Filter
            if etype in ALLOWED_TYPES:
                status_marker = "" 
                if e.get('status') != 'Enabled':
                    status_marker = f" [{e.get('status')}]"
                
                results.append({
                    'id': e['id'],
                    'text': f"[{etype}] {ename} (Ext: {enum}){status_marker}",
                    'type': etype
                })

        # Deduplicate based on ID
        final_results = []
        seen_ids = set()
        for r in results:
            if r['id'] not in seen_ids:
                final_results.append(r)
                seen_ids.add(r['id'])
        
        # Sort: Call Queues & IVRs at the top
        def sort_priority(item):
            # Higher number = lower in list
            order = {'Site': 0, 'CallQueue': 1, 'IvrMenu': 2, 'Department': 3}
            return order.get(item['type'], 10)
            
        final_results.sort(key=sort_priority)

        return jsonify({'status': 'success', 'results': final_results})

    except Exception as e:
        print(f"[CRITICAL SEARCH ERROR] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401
    
    try:
        # Use the class-based generator from utils
        graph, logs = generate_mermaid_flow(ext_id)
        
        return jsonify({
            'status': 'success',
            'mermaid_graph': graph,
            'api_log': logs
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': []}), 500
