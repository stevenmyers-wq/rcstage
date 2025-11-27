# webapp/visualiser/routes.py
import sys
import time
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    if params is None: params = {}
    current_params = params.copy()
    current_params['perPage'] = 1000
    current_params['page'] = 1
    all_records = []
    
    print(f"[INFO] Fetching: {endpoint} Params: {params}", file=sys.stdout)
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            resp = rc_api_call(url)
            if not resp: break
            if 'records' in resp: all_records.extend(resp['records'])
            
            nav = resp.get('navigation', {})
            if nav.get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.1) # Increased safety
            else: break
        except Exception as e:
            print(f"[ERROR] Page failed: {e}", file=sys.stderr)
            break
            
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    
    # Dictionary to deduplicate by ID
    merged_results = {}
    
    try:
        # 1. FETCH PHONES
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        for p in phones:
            if p.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber', 'NumberPool']:
                num = p.get('phoneNumber', '')
                if not return_all and query not in num: continue
                if p.get('extension', {}).get('id'):
                    eid = str(p['extension']['id'])
                    merged_results[eid] = {
                        'id': eid,
                        'text': f"{num} ({p.get('usageType')})",
                        'type': 'PhoneNumber',
                        'sort': 0
                    }

        # 2. FETCH CALL QUEUES (Specific Endpoint - The Fix)
        # This guarantees we get queues even if the main extension list misses them
        queues = fetch_all_pages("/restapi/v1.0/account/~/call-queues")
        for q in queues:
            qid = str(q['id'])
            qname = q.get('name', 'Unknown Queue')
            qnum = str(q.get('extensionNumber', ''))
            
            if not return_all:
                if query not in qname.lower() and query not in qnum: continue
                
            merged_results[qid] = {
                'id': qid,
                'text': f"[CallQueue] {qname} (Ext: {qnum})",
                'type': 'CallQueue',
                'sort': 1
            }

        # 3. FETCH ALL EXTENSIONS (General Endpoint)
        # We assume queues are already found above, so we focus on users/IVRs here
        # But we still check everything just in case
        ext_params = {'status': 'Enabled,Disabled,NotActivated'} 
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension", ext_params)
        
        ALLOWED_TYPES = [
            'IvrMenu', 'Department', 'Site', 'ApplicationExtension', 
            'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited', 
            'Bot', 'Room', 'ParkLocation', 'SharedLinesGroup'
        ]
        
        for e in exts:
            eid = str(e['id'])
            
            # If we already found this ID (e.g. via Queue list), skip to avoid overwriting with generic data
            if eid in merged_results: continue
            
            ename = e.get('name', 'Unknown')
            enum = str(e.get('extensionNumber', ''))
            etype = e.get('type', 'Unknown')
            
            if not return_all:
                if query not in ename.lower() and query not in enum: continue
            
            if etype in ALLOWED_TYPES or etype == 'CallQueue': # Catch queues if missed by specific list
                status_mk = "" if e.get('status') == 'Enabled' else f" [{e.get('status')}]"
                merged_results[eid] = {
                    'id': eid,
                    'text': f"[{etype}] {ename} (Ext: {enum}){status_mk}",
                    'type': etype,
                    'sort': 2 if etype == 'IvrMenu' else 3
                }

        # Convert to list and sort
        final_list = list(merged_results.values())
        final_list.sort(key=lambda x: x['sort'])
        
        print(f"[SEARCH] Found {len(final_list)} targets.", file=sys.stdout)
        return jsonify({'status': 'success', 'results': final_list})

    except Exception as e:
        print(f"[SEARCH ERROR] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401
    
    try:
        graph, logs = generate_mermaid_flow(ext_id)
        return jsonify({'status': 'success', 'mermaid_graph': graph, 'api_log': logs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': []}), 500
