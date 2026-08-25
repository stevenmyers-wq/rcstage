# webapp/visualiser/routes.py
import sys
import time
import json
from flask import Blueprint, jsonify, request, Response
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
import io
import zipfile
from webapp.visualiser.utils import (
    generate_graph_flow, generate_graph_flow_multi, generate_graph_flow_separate,
)
from webapp.visualiser.vsdx_export import build_vsdx, build_vsdx_multi

viz_bp = Blueprint('visualiser', __name__)


@viz_bp.route('/api/rc/visualiser/debug/<ext_id>', methods=['GET'])
def visualiser_debug_rules(ext_id):
    """Diagnostic: dump the RAW RingCentral call-handling responses for one
    extension id so we can see exactly what each endpoint returns for a queue's
    after-hours / business-hours routing. Visit:
        /api/rc/visualiser/debug/<internal-extension-id>
    Returns pretty JSON with the v2 comm-handling state rules, the v1 shortcut
    rules, and the v1 detailed list side by side. Read-only."""
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401

    ext_id = str(ext_id).strip()

    def _get(url):
        # Cache-bust so we see the live value, and capture whatever comes back.
        sep = '&' if '?' in url else '?'
        try:
            resp = rc_api_call(f"{url}{sep}_={int(time.time()*1000)}")
            return resp
        except Exception as e:
            return {'__exception__': str(e)}

    endpoints = {
        'v2_after_hours':
            f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules/after-hours",
        'v2_work_hours':
            f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules/work-hours",
        'v2_states':
            f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/states",
        'v1_after_hours_rule':
            f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/after-hours-rule",
        'v1_business_hours_rule':
            f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule",
        'v1_rules_list':
            f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=true",
        # User-level "Forward all calls" (overrides normal handling when enabled).
        'v1_forward_all_calls':
            f"/restapi/v1.0/account/~/extension/{ext_id}/forward-all-calls",
        'v2_forward_all_state':
            f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/states/forward-all-calls",
        'v2_forward_all_rule':
            f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules/forward-all-calls",
    }

    out = {'ext_id': ext_id}
    for key, url in endpoints.items():
        out[key] = {'endpoint': url, 'response': _get(url)}

    return Response(
        json.dumps(out, indent=2, default=str),
        mimetype='application/json',
    )


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


_CATEGORY_BY_TYPE = {
    'PhoneNumber': 'Phone Number',
    'CallQueue': 'Call Queue',
    'Department': 'Call Queue',
    'IvrMenu': 'IVR Menu',
    'Site': 'Site',
    'AnnouncementOnly': 'Announcement',
    'User': 'User / Agent',
    'DigitalUser': 'User / Agent',
    'VirtualUser': 'User / Agent',
    'FlexibleUser': 'User / Agent',
    'Limited': 'User / Agent',
    'ApplicationExtension': 'User / Agent',
    'Bot': 'User / Agent',
    'Room': 'Other',
    'ParkLocation': 'Other',
    'SharedLinesGroup': 'Other',
}


def _category_for(rc_type):
    return _CATEGORY_BY_TYPE.get(rc_type, 'Other')


def _site_name(obj):
    """Best-effort site name for an extension/queue record; defaults to
    'Main Site' to match the UAT generator's grouping convention."""
    site = obj.get('site') or {}
    name = site.get('name') if isinstance(site, dict) else None
    return name or 'Main Site'


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
                            'category': 'Phone Number',
                            'site': 'Main Site',
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
                    'category': 'Call Queue',
                    'site': _site_name(q),
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
                    'category': _category_for(etype),
                    'site': _site_name(e),
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


@viz_bp.route('/api/rc/trace-flow-multi', methods=['GET', 'POST'])
@track_usage('Call Flow Visualiser')
def visualize_call_flow_multi_api():
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401

    # Accept ids from JSON body (POST) or comma-separated query param (GET)
    ids = []
    show_inactive = False
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        ids = payload.get('ids') or []
        show_inactive = bool(payload.get('show_inactive', False))
    if not ids:
        raw = request.args.get('ids', '')
        ids = [p for p in raw.split(',') if p.strip()]
        show_inactive = request.args.get('show_inactive', '').lower() in ('1', 'true', 'yes')

    ids = [str(i).strip() for i in ids if str(i).strip()]
    if not ids:
        return jsonify({'status': 'error',
                        'message': 'No entry points supplied.',
                        'api_log': []}), 400

    try:
        graph_data, logs = generate_graph_flow_multi(ids, show_inactive=show_inactive)
        return jsonify({
            'status': 'success',
            'graph_data': graph_data,
            'api_log': logs,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'api_log': []}), 500


@viz_bp.route('/api/rc/trace-flow-separate', methods=['POST'])
@track_usage('Call Flow Visualiser')
def visualize_call_flow_separate_api():
    """Trace several entry points but keep each flow independent — one graph
    per entry — for the separate-canvas view."""
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401

    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids') or []
    show_inactive = bool(payload.get('show_inactive', False))
    ids = [str(i).strip() for i in ids if str(i).strip()]
    if not ids:
        return jsonify({'status': 'error', 'message': 'No entry points supplied.'}), 400

    try:
        flows = generate_graph_flow_separate(ids, show_inactive=show_inactive)
        return jsonify({'status': 'success', 'flows': flows})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@viz_bp.route('/api/rc/visualiser/export-visio', methods=['POST'])
def export_visio():
    """Build a Visio .vsdx from the already-traced graph data POSTed by the
    client. Kept separate from tracing so the export uses exactly what's on
    screen (respecting the current layout/entry points) without re-tracing."""
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401

    payload = request.get_json(silent=True) or {}
    graph_data = payload.get('graph_data') or {}
    if not graph_data.get('nodes'):
        return jsonify({'status': 'error', 'message': 'No graph data supplied.'}), 400

    filename = payload.get('filename') or 'call-flow'
    include_desc = bool(payload.get('include_descriptors', False))
    render = payload.get('render')
    # Sanitise the filename to a safe basename
    safe = ''.join(c if c.isalnum() or c in ('-', '_') else '_'
                   for c in str(filename))[:80] or 'call-flow'

    try:
        data = build_vsdx(graph_data, include_descriptors=include_desc, render=render)
    except Exception as e:
        print(f"[VISIO EXPORT] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    return Response(
        data,
        mimetype='application/vnd.ms-visio.drawing',
        headers={
            'Content-Disposition': f'attachment; filename="{safe}.vsdx"',
            'Content-Length': str(len(data)),
        },
    )


def _safe_name(name, fallback='call-flow'):
    safe = ''.join(c if c.isalnum() or c in ('-', '_') else '_'
                   for c in str(name))[:80]
    return safe or fallback


@viz_bp.route('/api/rc/visualiser/export-visio-multi', methods=['POST'])
def export_visio_multi():
    """Build a single multi-page .vsdx — one tab per supplied flow — so it
    imports into Lucid as one document with multiple pages."""
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Auth failed'}), 401

    payload = request.get_json(silent=True) or {}
    flows = payload.get('flows') or []
    include_desc = bool(payload.get('include_descriptors', False))
    if not flows:
        return jsonify({'status': 'error', 'message': 'No flows supplied.'}), 400

    try:
        page_flows = []
        for i, flow in enumerate(flows, start=1):
            gd = flow.get('graph_data') or {}
            if not gd.get('nodes'):
                continue
            page_flows.append({
                'name': (flow.get('name') or flow.get('filename') or f'Flow {i}')[:60],
                'graph_data': gd,
                'render': flow.get('render'),
                'include_descriptors': include_desc,
            })
        if not page_flows:
            return jsonify({'status': 'error', 'message': 'No flows with data.'}), 400
        data = build_vsdx_multi(page_flows)
    except Exception as e:
        print(f"[VISIO MULTI EXPORT] {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    return Response(
        data,
        mimetype='application/vnd.ms-visio.drawing',
        headers={
            'Content-Disposition': 'attachment; filename="call-flows.vsdx"',
            'Content-Length': str(len(data)),
        },
    )
