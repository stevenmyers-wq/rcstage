import io
import json
import threading
import time
from datetime import datetime
import pandas as pd
from flask import (Blueprint, request, jsonify, send_file, Response,
                   stream_with_context, current_app, session)
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from webapp import task_control
from .utils import (
    AUDIT_COLS, DEFAULT_STATE_RULE_ID, V2_STATE_RULE_BASE,
    ring_time_progress_store, run_ring_time_audit_background,
    apply_progress_store, run_ring_time_apply_background,
    add_ring_dropdown, autosize,
    fetch_extension_devices, ring_devices_for_ext, get_extension_id,
    get_default_ring_config, apply_ring_time, coerce_ring_seconds,
)
from . import storage

deskphone_ring_time_bp = Blueprint('deskphone_ring_time_bp', __name__,
                                   url_prefix='/api/deskphone_ring_time')
# Cooperative-stop endpoint for the streaming Apply run (mirrors Custom Rules).
deskphone_ring_time_bp.add_url_rule('/cancel', 'deskphone_ring_time_cancel',
                                    task_control.cancel_view, methods=['POST'])


def _session_auth_data():
    """Bundles the auth material a background thread needs to keep calling RC
    after the request (and its session) is gone — including a self-healing token
    refresh path. Mirrors Device Ringing Audit."""
    return {
        'access_token': get_rc_access_token(),
        'refresh_token': session.get('rc_refresh_token'),
        'client_id': session.get('rc_current_client_id'),
        'server_url': current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com'),
        'sm_employee_token': session.get('sm_employee_token'),
        'sm_employee_refresh_token': session.get('sm_employee_refresh_token'),
        'sm_target_id': session.get('sm_target_id'),
    }


# --- AUDIT: background task + poll -----------------------------------------
@deskphone_ring_time_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Deskphone Ring Time Audit')
def start_audit():
    """Kicks off the account crawl in a background thread and returns a task id.
    The browser then polls /audit/status and downloads via /audit/download. This
    keeps each HTTP request short so a large audit can't hit the Cloud Run
    request timeout the way a single long streaming response would."""
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401

    auth_data = _session_auth_data()
    task_id = f"drt_audit_{int(time.time())}"

    # Pre-initialise the store so the first status poll never 404s.
    ring_time_progress_store[task_id] = {
        'status': 'running', 'current': 0, 'total': 0,
        'message': 'Starting background task…',
        'file_data': None, 'rows': 0, 'error': None,
    }

    # Pass the real app object so the thread can push an app context, and the
    # signed-in user's email so the finished audit can be indexed for the
    # "Recent audits" list and downloaded later from any instance.
    app = current_app._get_current_object()
    user_email = session.get('user_email')
    thread = threading.Thread(
        target=run_ring_time_audit_background,
        args=(app, task_id, auth_data, user_email))
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "task_id": task_id})


@deskphone_ring_time_bp.route('/audit/status', methods=['GET'])
@require_rc_token
def audit_status():
    task_id = request.args.get('task_id')
    data = ring_time_progress_store.get(task_id)
    if data is not None:
        return jsonify({
            'current': data.get('current', 0),
            'total': data.get('total', 0),
            'status': data.get('status', 'running'),
            'message': data.get('message', 'Initializing…'),
            'rows': data.get('rows', 0),
            'error': data.get('error', ''),
        })

    # Not in this instance's memory (poll landed on another instance, or the
    # container was recycled). Fall back to the durable Firestore record.
    rec = storage.get_record(task_id) if task_id else None
    if rec:
        return jsonify({
            'current': rec.get('rows', 0), 'total': rec.get('rows', 0),
            'status': rec.get('status', 'running'),
            'message': 'Audit complete.' if rec.get('status') == 'completed' else 'Working…',
            'rows': rec.get('rows', 0),
            'error': rec.get('error', ''),
        })
    return jsonify({'current': 0, 'total': 0, 'status': 'running',
                    'message': 'Initializing…', 'rows': 0, 'error': ''})


@deskphone_ring_time_bp.route('/audit/download', methods=['GET'])
@require_rc_token
def audit_download():
    task_id = request.args.get('task_id')
    data = ring_time_progress_store.get(task_id, {})

    # Fast path: the file is still in this instance's memory.
    if data.get('status') == 'completed' and data.get('file_data'):
        return send_file(
            io.BytesIO(data['file_data']), as_attachment=True,
            download_name=data.get('filename') or f"Deskphone_Ring_Time_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    # Durable path: pull it from GCS (works from any instance, after tab close).
    file_bytes, filename = storage.load_file(task_id) if task_id else (None, None)
    if file_bytes:
        return send_file(
            io.BytesIO(file_bytes), as_attachment=True,
            download_name=filename or f"Deskphone_Ring_Time_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return "File not ready or expired", 404


@deskphone_ring_time_bp.route('/audit/recent', methods=['GET'])
@require_rc_token
def audit_recent():
    """Lists the signed-in user's recent completed audits (durable storage), so
    they can grab a result after closing the tab. Empty when storage is off."""
    email = session.get('user_email')
    return jsonify({'records': storage.list_recent(email) if email else []})


# --- APPLY ROUTE -----------------------------------------------------------
@deskphone_ring_time_bp.route('/apply', methods=['POST'])
@require_rc_token
@track_usage('Deskphone Ring Time Apply')
def apply_ring_times():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    task_id = request.form.get('task_id')
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file, sheet_name=(request.form.get('sheet_name') or 0))
        df.columns = df.columns.str.strip()
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    if 'Ext Number' not in df.columns:
        found = ', '.join(str(c) for c in df.columns) or '(none)'
        return jsonify({"error": f"The sheet has no 'Ext Number' column. Found columns: {found}. "
                                 f"Upload a 'Ring Times' audit export or the blank template."}), 400
    if 'New Ring Time (Secs)' not in df.columns:
        return jsonify({"error": "The sheet has no 'New Ring Time (Secs)' column to apply."}), 400

    # Group edited rows by extension so each extension's default rule is fetched
    # and PUT back exactly once, even when it has several deskphone/SIP devices.
    groups = {}
    for index, row in df.iterrows():
        raw_ext = row.get('Ext Number')
        if pd.isna(raw_ext):
            continue
        secs = coerce_ring_seconds(row.get('New Ring Time (Secs)'))
        if secs is None:
            continue  # blank / unparseable -> no change requested for this row
        ext_key = str(raw_ext).strip()
        if ext_key.endswith('.0'):
            ext_key = ext_key[:-2]
        g = groups.setdefault(ext_key, {'devices': {}, 'apply_all_secs': None})
        raw_did = row.get('Device ID')
        if pd.isna(raw_did) or not str(raw_did).strip():
            g['apply_all_secs'] = secs
        else:
            did = str(raw_did).strip()
            if did.endswith('.0'):
                did = did[:-2]
            g['devices'][did] = secs

    total = len(groups)
    if total == 0:
        return jsonify({"error": "No rows with a 'New Ring Time (Secs)' value to apply. "
                                 "Fill that column and re-upload."}), 400

    # Run the apply in a background thread and let the browser poll — a bulk apply
    # (with account-lock waits) can run for a long time, and a single streaming
    # request was being killed at the Cloud Run 3600s request timeout (~1h).
    task_id = task_id or f"drt_apply_{int(time.time())}"
    apply_progress_store[task_id] = {
        'status': 'running', 'current': 0, 'total': total,
        'message': 'Starting apply…', 'logs': [], 'written': 0,
        'cancelled': False, 'error': None,
    }
    auth_data = _session_auth_data()
    app = current_app._get_current_object()
    user_email = session.get('user_email')
    thread = threading.Thread(
        target=run_ring_time_apply_background,
        args=(app, task_id, groups, auth_data, user_email))
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "task_id": task_id, "total": total})


@deskphone_ring_time_bp.route('/apply/status', methods=['GET'])
@require_rc_token
def apply_status():
    """Progress + new log lines for a background apply. `since` is the number of
    log lines the browser has already shown, so each poll returns only new ones."""
    task_id = request.args.get('task_id')
    try:
        since = int(request.args.get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    data = apply_progress_store.get(task_id)
    if data is None:
        # Unknown to this instance (recycled, or landed on another instance).
        return jsonify({'status': 'unknown', 'current': 0, 'total': 0,
                        'message': '', 'logs': [], 'log_count': 0,
                        'written': 0, 'cancelled': False, 'error': ''})
    logs = data.get('logs', [])
    return jsonify({
        'status': data.get('status', 'running'),
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'message': data.get('message', ''),
        'logs': logs[since:],
        'log_count': len(logs),
        'written': data.get('written', 0),
        'cancelled': data.get('cancelled', False),
        'error': data.get('error', ''),
    })


# --- BLANK TEMPLATE --------------------------------------------------------
@deskphone_ring_time_bp.route('/template', methods=['GET'])
def download_template():
    """A blank sheet with the correct headers + the ring-time dropdown, for
    operators who prefer to type extensions/devices by hand rather than audit."""
    df_template = pd.DataFrame([], columns=AUDIT_COLS)
    instructions = [
        {"Field": "Ext Number", "Required": "Yes", "Notes": "The user extension the device belongs to."},
        {"Field": "Device ID", "Required": "Recommended", "Notes": "Device id from the audit export. Leave blank to apply to ALL deskphone / generic-SIP devices on the extension."},
        {"Field": "New Ring Time (Secs)", "Required": "Yes", "Notes": "Pick from the dropdown (5s steps up to 60s ≈ 12 rings). Blank = leave unchanged. ~5 seconds per ring."},
        {"Field": "Current Ring Time (Secs)", "Required": "No", "Notes": "Informational only (populated by the audit). Ignored on upload."},
        {"Field": "Schema", "Required": "No", "Notes": "Informational: V2 (comm-handling) or V1 (answering-rule) — the API used. Ignored on upload."},
        {"Field": "Scope", "Required": "—", "Notes": "Applies to the DEFAULT (business-hours) call-handling state only."},
    ]
    df_instructions = pd.DataFrame(instructions)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_template.to_excel(writer, index=False, sheet_name='Ring Times')
        ws1 = writer.sheets['Ring Times']
        add_ring_dropdown(ws1, AUDIT_COLS)
        autosize(ws1)

        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        autosize(writer.sheets['Format Guide'])

    output.seek(0)
    return send_file(
        output,
        download_name="deskphone_ring_time_template.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# --- DEBUG ROUTE -----------------------------------------------------------
def _dump(resp):
    """Response object -> JSON body on 2xx, else a compact status/body dict."""
    if getattr(resp, 'ok', False):
        try:
            return resp.json()
        except Exception:
            return {"note": "ok but non-JSON body"}
    return {"status": getattr(resp, 'status_code', '?'),
            "body": ((getattr(resp, 'text', '') or '').strip())[:600]}


@deskphone_ring_time_bp.route('/debug', methods=['GET'])
@require_rc_token
def debug_ring_time():
    """Ground-truth dump used to pin down the real V2/V1 ring-time schema on a
    live account: an extension's devices, the FULL V2 comm-handling state-rule
    list AND each rule fetched by id (the list returns dispatching only as a
    `dispatchingRef`, so per-rule detail is needed to see the RingGroupAction
    durations), the V1 answering-rule list + business-hours detail, and a
    durable-storage self-check. Visit /api/deskphone_ring_time/debug?ext=101
    while signed in. Add &full=1 to include full rule bodies (large)."""
    ext_num = (request.args.get('ext') or '').strip()
    full = request.args.get('full') in ('1', 'true', 'yes')
    if not ext_num:
        return jsonify({"error": "Pass ?ext=<extension number>, e.g. ?ext=101"}), 400

    out = {"ext_requested": ext_num, "storage": storage.diagnostics()}
    try:
        ext_id = get_extension_id(ext_num)
        if not ext_id:
            return jsonify({"error": f"Extension {ext_num} not found."}), 404
        out["ext_id"] = ext_id

        dev = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device",
                          return_response=True)
        dev_body = _dump(dev)
        out["devices"] = [
            {"id": d.get("id"), "type": d.get("type"), "name": d.get("name")}
            for d in (dev_body.get("records", []) if isinstance(dev_body, dict) else [])
        ] or dev_body

        # --- V2 comm-handling: LIST the state rules, then GET each by id -----
        v2_list_url = V2_STATE_RULE_BASE.format(ext_id=ext_id)
        v2_list_resp = rc_api_call(v2_list_url, return_response=True)
        v2_list = _dump(v2_list_resp)
        out["v2_state_rules_list"] = v2_list
        out["v2_state_rules_detail"] = []
        if isinstance(v2_list, dict):
            for rec in v2_list.get("records", []):
                rid = rec.get("id")
                if not rid:
                    continue
                detail = _dump(rc_api_call(f"{v2_list_url}/{rid}", return_response=True))
                if full:
                    out["v2_state_rules_detail"].append({"id": rid, "detail": detail})
                else:
                    # Compact: just enough to see how the default rule is keyed
                    # and where the ring durations live.
                    dispatching = (detail.get("dispatching") if isinstance(detail, dict) else None) or {}
                    ring_actions = []
                    for a in dispatching.get("actions", []):
                        if a.get("type") == "RingGroupAction":
                            ring_actions.append({
                                "enabled": a.get("enabled"),
                                "duration": a.get("duration"),
                                "targets": [
                                    {"type": t.get("type"),
                                     "device_id": (t.get("device") or {}).get("id")}
                                    for t in a.get("targets", [])
                                ],
                            })
                    out["v2_state_rules_detail"].append({
                        "id": rid,
                        "displayName": rec.get("displayName") or rec.get("name"),
                        "state": rec.get("state"),
                        "type": rec.get("type"),
                        "enabled": rec.get("enabled"),
                        "dispatching_type": dispatching.get("type"),
                        "ring_group_actions": ring_actions,
                    })

        # --- V1 answering rules: list + business-hours detail ---------------
        v1_list = _dump(rc_api_call(
            f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule",
            return_response=True))
        out["v1_answering_rules_list"] = v1_list
        v1_bh = rc_api_call(
            f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{DEFAULT_STATE_RULE_ID}",
            params={'view': 'Detailed'}, return_response=True)
        out["v1_business_hours_rule"] = _dump(v1_bh)

        schema, durations, _ = get_default_ring_config(ext_id)
        out["parsed_by_current_code"] = {"schema": schema, "device_durations_secs": durations}
    except Exception as e:
        out["error"] = str(e)

    return current_app.response_class(json.dumps(out, indent=2, default=str),
                                      mimetype='application/json')
