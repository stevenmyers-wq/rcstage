# webapp/visualiser/routes.py
import sys
import time
from flask import Blueprint, jsonify, request
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_graph_flow

viz_bp = Blueprint('visualiser', __name__)


def fetch_all_pages(endpoint, params=None):
    if params is None:
        params = {}
    current_params = params.copy()
    current_params['perPage'] = 250
    current_params['page'] = 1
    all_records = []

    while True:
        try:
            query_string = "&".join([f"{k}={v}" for k, v in current_params.items()])
            sep = '&' if '?' in endpoint else '?'
            url = f"{endpoint}{sep}{query_string}"
            resp = rc_api_call(url)
            if not resp:
                break
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

    return all_records


@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401

    query = request.args.get('query', '').lower().strip()
    return_all = (len(query) == 0)

    results_map = {}

    try:
        # --- Phone numbers ---
        phones = fetch_all_pages("/restapi/v1.0/account/~/phone-number")
        phone_map = {}  # ext_id -> [numbers]

        for p in phones:
            p_num = p.get('phoneNumber', '')
            usage = p.get('usageType', '')
            ext_id = str(p.get('extension', {}).get('id', ''))

            if ext_id and ext_id != 'None':
                phone_map.setdefault(ext_id, []).append(p_num)
            else:
                if usage in ['MainCompanyNumber', 'DirectNumber', 'CompanyNumber']:
                    if return_all or query in p_num:
                        # Use company_ prefix for numbers that route to main site
                        # so the tracer knows not to follow the extension RC returns
                        if usage in ['CompanyNumber', 'MainCompanyNumber']:
                            pid = f"company_{p_num}"
                        else:
                            pid = f"ext_{p_num}"
                        results_map[pid] = {
                            'id': pid,
                            'text': f"📞 {p_num} ({usage})",
                            'name': p_num,
                            'type': 'PhoneNumber',
                            'sort_group': 0,
                        }

        # --- Call queues ---
        queues = fetch_all_pages("/restapi/v1.0/account/~/call-queues")
        for q in queues:
            qid = str(q['id'])
            qname = q.get('name', 'Unknown Queue')
            qnum = str(q.get('extensionNumber', ''))

            match = return_all
            if not match:
                if query in qname.lower() or query in qnum:
                    match = True
                for ph in phone_map.get(qid, []):
                    if query in ph:
                        match = True

            if match:
                phone_txt = f" 📞 {', '.join(phone_map.get(qid, []))}" if qid in phone_map else ""
                results_map[qid] = {
                    'id': qid,
                    'text': f"👥 {qname} (Ext: {qnum}){phone_txt}",
                    'name': qname,
                    'type': 'CallQueue',
                    'sort_group': 1,
                }

        # --- All extensions ---
        exts = fetch_all_pages("/restapi/v1.0/account/~/extension")

        ALLOWED_TYPES = [
            'IvrMenu', 'Department', 'Site', 'AnnouncementOnly',
            'ApplicationExtension', 'User', 'DigitalUser', 'VirtualUser',
            'FlexibleUser', 'Limited', 'Bot', 'Room', 'ParkLocation',
            'SharedLinesGroup'
        ]

        for e in exts:
            eid = str(e['id'])
            if eid in results_map:
                continue

            etype = e.get('type', 'Unknown')
            if etype not in ALLOWED_TYPES:
                continue

            ename = e.get('name', 'Unknown')
            enum = str(e.get('extensionNumber', ''))

            match = return_all
            if not match:
                if query in ename.lower() or query in enum:
                    match = True
                for ph in phone_map.get(eid, []):
                    if query in ph:
                        match = True

            if match:
                status_mk = "" if e.get('status') == 'Enabled' else f" [{e.get('status')}]"
                phone_txt = f" 📞 {', '.join(phone_map.get(eid, []))}" if eid in phone_map else ""

                if etype == 'IvrMenu':
                    icon = "🤖"
                    sort_group = 2
                elif etype in ('AnnouncementOnly', 'Site'):
                    icon = "🏢"
                    sort_group = 2
                else:
                    icon = "👤"
                    sort_group = 3

                results_map[eid] = {
                    'id': eid,
                    'text': f"{icon} [{etype}] {ename} (Ext: {enum}){phone_txt}{status_mk}",
                    'name': ename,
                    'type': etype,
                    'sort_group': sort_group,
                }

        if not results_map:
            results_map['err'] = {
                'id': 'err',
                'text': '⚠️ No extensions found',
                'name': '',
                'type': '',
                'sort_group': 99,
            }

        # Sort: by group first, then alphabetically by name within each group
        final_list = sorted(
            results_map.values(),
            key=lambda x: (x['sort_group'], x['name'].lower())
        )

        return jsonify({'status': 'success', 'results': final_list})

    except Exception as e:
        print(f"[SEARCH CRASH] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
@track_usage('Call Flow Visualiser')
def visualize_call_flow_api(ext_id):
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401

    try:
        graph_data, logs = generate_graph_flow(ext_id)
        return jsonify({
            'status': 'success',
            'graph_data': graph_data,
            'api_log': logs,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': []}), 500
