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
    
    print(f"[INFO] Fetching: {endpoint} | Params: {params}", file=sys.stdout)
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            resp = rc_api_call(url)
            
            if not resp: break
            if 'records' in resp: 
                all_records.extend(resp['records'])
                
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
    
    results_map = {}
    
    try:
        # --- STEP 1: MAP PHONES (Do not add assigned numbers to results yet) ---
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        phone_map = {} # { ext_id: [numbers] }
        
        for p in phones:
            p_num = p.get('phoneNumber', '')
            usage = p.get('usageType', '')
            
            # Map to Extension if assigned
            ext_id = str(p.get('extension', {}).get('id', ''))
            
            if ext_id and ext_id != 'None':
                if ext_id not in phone_map: phone_map[ext_id] = []
                phone_map[ext_id].append(p_num)
            else:
                # UNASSIGNED / MAIN NUMBERS: Add these immediately
                if usage in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber', 'NumberPool']:
                    if return_all or query in p_num:
                        pid = f"ext_{p_num}"
                        results_map[pid] = {
                            'id': pid,
                            'text': f"📞 {p_num} ({usage})",
                            'type': 'PhoneNumber',
                            'sort': 99
                        }

        # --- STEP 2: CALL QUEUES (Specific Fetch) ---
        queues = fetch_all_pages("/restapi/v1.0/account/~/call-queues")
        for q in queues:
            qid = str(q['id'])
            qname = q.get('name', 'Unknown Queue')
            qnum = str(q.get('extensionNumber', ''))
            
            # Attach Phones
            assigned_phones = phone_map.get(qid, [])
            phone_label = f" 📞 {', '.join(assigned_phones)}" if assigned_phones else ""
            
            # Filter
            match = return_all
            if not match:
                if query in qname.lower() or query in qnum: match = True
                for ph in assigned_phones:
                    if query in ph: match = True
            
            if match:
                results_map[qid] = {
                    'id': qid,
                    'text': f"👥 [CallQueue] {qname} (Ext: {qnum}){phone_label}",
                    'type': 'CallQueue',
                    'sort': 1
                }

        # --- STEP 3: ALL EXTENSIONS (Users, IVRs, etc) ---
        # Fetch Enabled AND Disabled
        ext_params = {'status': 'Enabled,Disabled'} 
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension", ext_params)
        
        ALLOWED_TYPES = [
            'IvrMenu', 'Department', 'Site', 'ApplicationExtension', 
            'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited', 
            'Bot', 'Room', 'ParkLocation', 'SharedLinesGroup'
        ]
        
        for e in exts:
            eid = str(e['id'])
            
            # If ID already exists (from Queue fetch), SKIP IT to preserve Queue formatting
            if eid in results_map: continue
            
            etype = e.get('type', 'Unknown')
            if etype not in ALLOWED_TYPES: continue
            
            ename = e.get('name', 'Unknown')
            enum = str(e.get('extensionNumber', ''))
            
            # Attach Phones
            assigned_phones = phone_map.get(eid, [])
            phone_label = f" 📞 {', '.join(assigned_phones)}" if assigned_phones else ""
            
            # Filter
            match = return_all
            if not match:
                if query in ename.lower() or query in enum: match = True
                for ph in assigned_phones:
                    if query in ph: match = True
            
            if match:
                status_mk = "" if e.get('status') == 'Enabled' else f" [{e.get('status')}]"
                
                # Icons
                icon = "👤"
                if etype == 'IvrMenu': icon = "🤖"
                elif etype == 'Site': icon = "🏢"
                
                results_map[eid] = {
                    'id': eid,
                    'text': f"{icon} [{etype}] {ename} (Ext: {enum}){phone_label}{status_mk}",
                    'type': etype,
                    'sort': 2 if etype == 'IvrMenu' else 3
                }

        # Convert to list and sort
        final_list = list(results_map.values())
        final_list.sort(key=lambda x: x['sort'])
        
        print(f"[SEARCH] Returning {len(final_list)} items.", file=sys.stdout)
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
