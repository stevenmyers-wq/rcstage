# webapp/visualiser/routes.py
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow
import time

viz_bp = Blueprint('visualiser', __name__)

def fetch_all_pages(endpoint, params=None):
    if params is None: params = {}
    current_params = params.copy()
    current_params['perPage'] = 1000
    current_params['page'] = 1
    all_records = []
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            separator = '&' if '?' in endpoint else '?'
            response = rc_api_call(f"{endpoint}{separator}{query_string}")
            
            if not response: break
            if 'records' in response: all_records.extend(response['records'])
            
            if response.get('navigation', {}).get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.1)
            else: break
        except: break
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
            if not return_all and query not in p_number: continue
            
            if record.get('extension'):
                # CLEAN TEXT - NO ICONS
                results.append({
                    'id': record['extension']['id'],
                    'text': f"{p_number} ({record.get('usageType')})",
                    'type': 'PhoneNumber'
                })

    # 2. Extensions
    ext_records = fetch_all_pages("/restapi/v1.0/account/~/extension")
    for ext in ext_records:
        e_name = ext.get('name', 'Unknown')
        e_number = ext.get('extensionNumber', '')
        e_type = ext.get('type', 'Unknown')
        
        if not return_all:
             if query not in e_name.lower() and query != e_number: continue
        
        if e_type in ['IvrMenu', 'CallQueue', 'Department', 'Site', 'User', 'ApplicationExtension']:
            
            status = "" if ext.get('status') == 'Enabled' else " [Disabled]"
            
            # CLEAN TEXT - Format: "[Type] Name (Ext)"
            results.append({
                'id': ext['id'],
                'text': f"[{e_type}] {e_name} (Ext: {e_number}){status}",
                'type': e_type
            })
    
    # Deduplicate & Sort
    final_results = []
    seen = set()
    for item in results:
        if item['id'] not in seen:
            final_results.append(item)
            seen.add(item['id'])
            
    final_results.sort(key=lambda x: {'Site':0, 'IvrMenu':1, 'CallQueue':2}.get(x['type'], 5))

    return jsonify({'status': 'success', 'results': final_results})

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    session['api_log'] = []
    try:
        mermaid = generate_mermaid_flow(ext_id)
        return jsonify({'status': 'success', 'mermaid_graph': mermaid, 'api_log': session.pop('api_log', [])})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': session.pop('api_log', [])}), 500
