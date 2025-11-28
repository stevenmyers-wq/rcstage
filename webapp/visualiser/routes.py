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
    Robust Paginator with Rate Limit Handling.
    """
    if params is None: params = {}
    current_params = params.copy()
    current_params['perPage'] = 500 # Safe batch size
    current_params['page'] = 1
    
    all_records = []
    
    print(f"[INFO] Fetching: {endpoint} | Params: {params}", file=sys.stdout)
    
    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            
            resp = rc_api_call(url)
            
            # 1. Handle Rate Limiting / Errors
            if isinstance(resp, dict) and resp.get('errorCode') == 'CMN-429':
                print(f"[WARN] Rate Limit Hit. Sleeping 2s...", file=sys.stderr)
                time.sleep(2.0)
                continue # Retry same page
                
            if not resp or 'records' not in resp:
                print(f"[WARN] Page {current_params['page']} empty or failed.", file=sys.stderr)
                break

            # 2. Collect Data
            all_records.extend(resp['records'])
            
            # 3. Next Page Logic
            nav = resp.get('navigation', {})
            if nav.get('nextPage'):
                current_params['page'] += 1
                time.sleep(0.05) # Tiny pause to be nice
            else:
                break
                
        except Exception as e:
            print(f"[ERROR] Pagination Loop Error: {e}", file=sys.stderr)
            break
            
    print(f"[INFO] Finished {endpoint}. Total: {len(all_records)}", file=sys.stdout)
    return all_records

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)
    
    # We use a Dict to deduplicate by ID automatically
    # Key: ID, Value: Item Dict
    results_map = {}
    
    try:
        # --- STEP 1: PHONE NUMBERS (Map DIDs to Extensions) ---
        # This helps us search by phone number later
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        phone_map = {} # { ext_id: [numbers] }
        
        for p in phones:
            p_num = p.get('phoneNumber', '')
            usage = p.get('usageType', '')
            
            # Add standalone DID result
            if usage in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber', 'NumberPool']:
                if return_all or query in p_num:
                    # If it's not assigned to an extension, add it as a standalone node
                    if not p.get('extension'):
                        pid = f"ext_{p_num}"
                        results_map[pid] = {
                            'id': pid,
                            'text': f"📞 {p_num} ({usage})",
                            'type': 'PhoneNumber',
                            'sort': 99
                        }

            # Map to Extension if assigned
            if p.get('extension', {}).get('id'):
                eid = str(p['extension']['id'])
                if eid not in phone_map: phone_map[eid] = []
                phone_map[eid].append(p_num)

        # --- STEP 2: ALL EXTENSIONS (Users, IVRs, Queues) ---
        # We assume this is the "Source of Truth"
        ext_params = {'status': 'Enabled,Disabled,NotActivated'} 
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension", ext_params)
        
        ALLOWED = [
            'User', 'DigitalUser', 'VirtualUser', 'FaxUser', 'FlexibleUser', 'Limited',
            'CallQueue', 'Department', 'IvrMenu', 'ApplicationExtension', 
            'Site', 'ParkLocation', 'SharedLinesGroup', 'Bot', 'Room'
        ]
        
        # TYPO FIXED HERE: was 'for e in exs:'
        for e in exts: 
            try:
                eid = str(e['id'])
                etype = e.get('type', 'Unknown')
                
                # Filter by Type
                if etype not in ALLOWED: continue
                
                ename = e.get('name', 'Unknown')
                enum = str(e.get('extensionNumber', ''))
                
                # Attach Phones
                my_phones = phone_map.get(eid, [])
                phone_txt = f" 📞 {', '.join(my_phones)}" if my_phones else ""
                
                # Search Filter
                match = return_all
                if not match:
                    if query in ename.lower(): match = True
                    elif query in enum: match = True
                    # Also search inside assigned phone numbers
                    elif any(query in ph for ph in my_phones): match = True
                
                if match:
                    # Format Status
                    status_mk = "" 
                    if e.get('status') != 'Enabled': status_mk = f" [{e.get('status')}]"
                    
                    # Icons for UX
                    icon = "👤"
                    if etype == 'CallQueue': icon = "👥"
                    elif etype == 'IvrMenu': icon = "🤖"
                    elif etype == 'Site': icon = "🏢"
                    
                    results_map[eid] = {
                        'id': eid,
                        'text': f"{icon} [{etype}] {ename} (Ext: {enum}){phone_txt}{status_mk}",
                        'type': etype,
                        'sort': 1 if etype == 'CallQueue' else (2 if etype == 'IvrMenu' else 3)
                    }
            except Exception as inner_e:
                print(f"Skipping bad extension record: {inner_e}")
                continue

        # Convert map to list
        final_results = list(results_map.values())
        
        # Sort (Queues -> IVRs -> Users -> Others)
        final_results.sort(key=lambda x: x['sort'])
        
        print(f"[SEARCH] Returning {len(final_results)} items.", file=sys.stdout)
        return jsonify({'status': 'success', 'results': final_results})

    except Exception as e:
        print(f"[CRITICAL SEARCH ERROR] {e}", file=sys.stderr)
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
