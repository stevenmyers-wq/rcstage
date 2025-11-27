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
            # Construct URL manually
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            resp = rc_api_call(url)
            
            if not resp: break
            if 'records' in resp: 
                all_records.extend(resp['records'])
                
            # Pagination
            nav = resp.get('navigation', {})
            if nav.get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.05)
            else:
                break
        except Exception as e:
            print(f"[ERROR] Pagination failed: {e}", file=sys.stderr)
            break
            
    print(f"[INFO] Fetched {len(all_records)} records from {endpoint}", file=sys.stdout)
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    
    # Final list of objects
    final_results = []
    # Set to track processed IDs so we don't duplicate
    processed_ids = set()
    
    try:
        # --- STEP 1: MAP PHONES TO EXTENSIONS ---
        # We fetch ALL phones first so we can attach them to Queues/Users later.
        phone_map = {} # { 'ext_id': ['+1555...', '+1666...'] }
        
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        for p in phones:
            if p.get('usageType') in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber', 'NumberPool']:
                p_num = p.get('phoneNumber', '')
                ext_id = str(p.get('extension', {}).get('id', ''))
                
                if ext_id and ext_id != 'None':
                    if ext_id not in phone_map: phone_map[ext_id] = []
                    phone_map[ext_id].append(p_num)
                    
                # Also add unassigned/main numbers as standalone entries
                if not ext_id or ext_id == 'None':
                    # Only add if matching query
                    if not return_all and query not in p_num: continue
                    final_results.append({
                        'id': f"ext_{p_num}", # Synthetic ID
                        'text': f"📞 {p_num} ({p.get('usageType')})",
                        'type': 'PhoneNumber',
                        'sort': 0
                    })

        # --- STEP 2: FETCH CALL QUEUES ---
        # We fetch these explicitly because they are most important
        queues = fetch_all_pages("/restapi/v1.0/account/~/call-queues")
        for q in queues:
            qid = str(q['id'])
            qname = q.get('name', 'Unknown Queue')
            qnum = str(q.get('extensionNumber', ''))
            
            # Get assigned phones
            assigned_phones = phone_map.get(qid, [])
            phone_label = f" 📞 {', '.join(assigned_phones)}" if assigned_phones else ""
            
            # Filter Logic (Name OR Ext OR Phone)
            match = return_all
            if not match:
                if query in qname.lower() or query in qnum: match = True
                for ph in assigned_phones:
                    if query in ph: match = True
            
            if match:
                final_results.append({
                    'id': qid,
                    'text': f"[CallQueue] {qname} (Ext: {qnum}){phone_label}",
                    'type': 'CallQueue',
                    'sort': 1
                })
                processed_ids.add(qid)

        # --- STEP 3: FETCH ALL EXTENSIONS ---
        # Catch Users, IVRs, and anything else missed
        ext_params = {'status': 'Enabled,Disabled,NotActivated'} 
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension", ext_params)
        
        ALLOWED_TYPES = [
            'IvrMenu', 'Department', 'Site', 'ApplicationExtension', 
            'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited', 
            'Bot', 'Room', 'ParkLocation', 'SharedLinesGroup'
        ]
        
        for e in exts:
            eid = str(e['id'])
            if eid in processed_ids: continue # Skip if already handled in Queue loop
            
            etype = e.get('type', 'Unknown')
            if etype not in ALLOWED_TYPES and etype != 'CallQueue': continue
            
            ename = e.get('name', 'Unknown')
            enum = str(e.get('extensionNumber', ''))
            
            # Get assigned phones
            assigned_phones = phone_map.get(eid, [])
            phone_label = f" 📞 {', '.join(assigned_phones)}" if assigned_phones else ""
            
            # Filter Logic
            match = return_all
            if not match:
                if query in ename.lower() or query in enum: match = True
                for ph in assigned_phones:
                    if query in ph: match = True
            
            if match:
                status_mk = "" if e.get('status') == 'Enabled' else f" [{e.get('status')}]"
                
                final_results.append({
                    'id': eid,
                    'text': f"[{etype}] {ename} (Ext: {enum}){phone_label}{status_mk}",
                    'type': etype,
                    'sort': 2 if etype == 'IvrMenu' else 3
                })
                processed_ids.add(eid)

        # Sort
        final_results.sort(key=lambda x: x['sort'])
        
        print(f"[SEARCH] Returning {len(final_results)} items.", file=sys.stdout)
        return jsonify({'status': 'success', 'results': final_results})

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
