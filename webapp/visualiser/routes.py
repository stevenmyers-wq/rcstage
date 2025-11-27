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
    current_params['perPage'] = 500 # Lowered from 1000 for stability
    current_params['page'] = 1
    all_records = []
    
    print(f"[INFO] Fetching: {endpoint}", file=sys.stdout)
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            resp = rc_api_call(url)
            if not resp: break
            if 'records' in resp: all_records.extend(resp['records'])
            
            if resp.get('navigation', {}).get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.05)
            else: break
        except Exception as e:
            print(f"[ERROR] Fetch failed: {e}", file=sys.stderr)
            break
            
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
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        for p in phones:
            if p.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
                num = p.get('phoneNumber', '')
                if not return_all and query not in num: continue
                if p.get('extension', {}).get('id'):
                    results.append({
                        'id': p['extension']['id'],
                        'text': f"{num} ({p.get('usageType')})",
                        'type': 'PhoneNumber'
                    })
        
        # 2. Extensions
        # Fetching basic info for ALL extensions
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension")
        
        # Expanded List of Allowed Types to catch missing extensions
        ALLOWED_TYPES = [
            'IvrMenu', 'CallQueue', 'Department', 'Site', 'ApplicationExtension',
            'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited', 'Bot'
        ]
        
        for e in exts:
            ename = e.get('name', 'Unknown')
            enum = e.get('extensionNumber', '')
            etype = e.get('type', 'Unknown')
            
            # Filter Logic
            if not return_all:
                if query not in ename.lower() and query != enum: continue
            
            if etype in ALLOWED_TYPES:
                status = "" if e.get('status') == 'Enabled' else " [Disabled]"
                results.append({
                    'id': e['id'],
                    'text': f"[{etype}] {ename} (Ext: {enum}){status}",
                    'type': etype
                })

        # Deduplicate
        final_results = []
        seen = set()
        for r in results:
            if r['id'] not in seen:
                final_results.append(r)
                seen.add(r['id'])
        
        final_results.sort(key=lambda x: {'Site':0, 'IvrMenu':1, 'CallQueue':2}.get(x['type'], 5))
        return jsonify({'status': 'success', 'results': final_results})

    except Exception as e:
        print(f"[ERROR] Search route: {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401
    session['api_log'] = []
    try:
        graph = generate_mermaid_flow(ext_id)
        return jsonify({'status': 'success', 'mermaid_graph': graph, 'api_log': session.pop('api_log', [])})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': session.pop('api_log', [])}), 500
