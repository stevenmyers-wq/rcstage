import io
import json
import base64
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context, current_app
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from webapp import task_control
from .utils import (
    ACCEPTED_RING_SECONDS, DEVICE_TYPE_LABELS, DEFAULT_STATE_RULE_ID,
    V2_STATE_RULE_BASE,
    fetch_all_users, fetch_all_devices, fetch_extension_devices,
    ring_devices_for_ext, device_display_name, get_extension_id,
    get_default_ring_config, apply_ring_time, coerce_ring_seconds,
)

deskphone_ring_time_bp = Blueprint('deskphone_ring_time_bp', __name__,
                                   url_prefix='/api/deskphone_ring_time')
# Cooperative-stop endpoint for the streaming Apply run (mirrors Custom Rules).
deskphone_ring_time_bp.add_url_rule('/cancel', 'deskphone_ring_time_cancel',
                                    task_control.cancel_view, methods=['POST'])

# Columns for the audit export AND the accepted upload layout — a row per
# deskphone / generic-SIP device. Editing the sheet in place and re-uploading is
# the intended workflow, so the two share one schema.
AUDIT_COLS = [
    'Ext Number', 'Ext Name', 'Site', 'Device Name', 'Device Type',
    'Device ID', 'Schema', 'Current Ring Time (Secs)', 'New Ring Time (Secs)',
]

# The one data-validated column: an operator may only pick an API-accepted ring
# time (5s steps up to 60s). Blank means "leave this device unchanged".
_RING_DROPDOWN_FORMULA = '"' + ','.join(str(s) for s in ACCEPTED_RING_SECONDS) + '"'


def _add_ring_dropdown(ws, columns, max_row=2000):
    """Attaches the accepted-seconds dropdown to the New Ring Time column."""
    if 'New Ring Time (Secs)' not in columns:
        return
    letter = get_column_letter(columns.index('New Ring Time (Secs)') + 1)
    dv = DataValidation(type="list", formula1=_RING_DROPDOWN_FORMULA, allow_blank=True)
    dv.error = 'Pick an accepted ring time in seconds (5s steps, up to 60).'
    dv.errorTitle = 'Invalid ring time'
    ws.add_data_validation(dv)
    dv.add(f"{letter}2:{letter}{max_row}")


def _autosize(ws):
    for column in ws.columns:
        length = max(len(str(cell.value) or "") for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(length + 5, 55)


# --- AUDIT ROUTE -----------------------------------------------------------
@deskphone_ring_time_bp.route('/audit', methods=['GET'])
@require_rc_token
@track_usage('Deskphone Ring Time Audit')
def audit_ring_times():
    """Streams progress while building an Excel report of every user's deskphone
    / generic-SIP ring time on the default call-handling state. Only users that
    actually own such a device are queried, keeping the crawl small."""

    def generate():
        try:
            yield json.dumps({"type": "status", "message": "Loading users…"}) + "\n"
            users = fetch_all_users()
            if not users:
                yield json.dumps({"type": "error", "message": "Failed to fetch users."}) + "\n"
                return

            yield json.dumps({"type": "status", "message": "Loading account devices…"}) + "\n"
            devices_by_ext = fetch_all_devices()

            # Only audit users that own a deskphone / generic-SIP device.
            targets = []
            for u in users:
                ext_id = str(u.get('id'))
                ring_devs = ring_devices_for_ext(devices_by_ext.get(ext_id, []))
                if ring_devs:
                    targets.append((u, ring_devs))

            total = len(targets)
            yield json.dumps({"type": "start", "total": total}) + "\n"

            audit_data = []
            for i, (user, ring_devs) in enumerate(targets):
                ext_id = str(user.get('id'))
                ext_num = user.get('extensionNumber', '')
                ext_name = user.get('name', 'Unknown')
                site = (user.get('site') or {}).get('name') or 'Main Site'

                try:
                    schema, durations, _ = get_default_ring_config(ext_id)
                except Exception:
                    schema, durations = None, {}

                for d in ring_devs:
                    did = str(d.get('id'))
                    current = durations.get(did)
                    audit_data.append({
                        'Ext Number': ext_num,
                        'Ext Name': ext_name,
                        'Site': site,
                        'Device Name': device_display_name(d),
                        'Device Type': DEVICE_TYPE_LABELS.get(d.get('type'), d.get('type')),
                        'Device ID': did,
                        'Schema': schema or 'Unknown',
                        'Current Ring Time (Secs)': current if current is not None else '',
                        'New Ring Time (Secs)': '',
                    })

                yield json.dumps({
                    "type": "progress", "current": i + 1, "total": total,
                    "message": str(ext_num or ext_id)
                }) + "\n"

            if not audit_data:
                audit_data = [{'Ext Number': 'No Data',
                               'Device Name': 'No deskphone / generic-SIP devices found'}]

            df = pd.DataFrame(audit_data)
            for c in AUDIT_COLS:
                if c not in df.columns:
                    df[c] = ''
            df = df[AUDIT_COLS]

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Ring Times')
                ws = writer.sheets['Ring Times']
                _add_ring_dropdown(ws, AUDIT_COLS)
                _autosize(ws)

            output.seek(0)
            file_b64 = base64.b64encode(output.read()).decode('ascii')
            filename = f"Deskphone_Ring_Time_{datetime.now().strftime('%Y%m%d')}.xlsx"
            yield json.dumps({
                "type": "complete", "filename": filename, "file_b64": file_b64,
                "rows": len(df)
            }) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


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
    # Each group carries: {device_id: seconds} explicitly named, plus a flag for
    # rows that left Device ID blank (apply to every deskphone/SIP device).
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
            # No specific device -> apply to all deskphone/SIP devices on the ext.
            g['apply_all_secs'] = secs
        else:
            did = str(raw_did).strip()
            if did.endswith('.0'):
                did = did[:-2]
            g['devices'][did] = secs

    total = len(groups)

    def generate():
        cancelled = False
        current = 0
        written_count = 0

        def prog(message=None, level='info'):
            evt = {"type": "progress", "current": current, "total": total}
            if message is not None:
                evt["message"] = message
                evt["level"] = level
            return json.dumps(evt) + "\n"

        yield json.dumps({"type": "start", "total": total}) + "\n"

        for ext_key, group in groups.items():
            if task_control.is_stopped(task_id):
                cancelled = True
                current = total
                yield prog("■ Stopped by user — remaining extensions were skipped.", "info")
                break

            current += 1
            try:
                ext_id = get_extension_id(ext_key)
                if not ext_id:
                    yield prog(f"Ext {ext_key}: ⚠️ extension not found.", "error")
                    continue

                device_targets = dict(group['devices'])

                # Resolve a blank-Device-ID row to every deskphone/SIP device on
                # the extension. Explicit per-device values take precedence.
                if group['apply_all_secs'] is not None:
                    ring_devs = ring_devices_for_ext(fetch_extension_devices(ext_id))
                    if not ring_devs:
                        yield prog(f"Ext {ext_key}: ⚠️ no deskphone / generic-SIP devices found.", "error")
                        if not device_targets:
                            continue
                    for d in ring_devs:
                        did = str(d.get('id'))
                        device_targets.setdefault(did, group['apply_all_secs'])

                if not device_targets:
                    yield prog(f"Ext {ext_key}: ⚠️ nothing to apply.", "info")
                    continue

                ok, schema, msg = apply_ring_time(ext_id, device_targets)
                if ok:
                    secs_shown = sorted(set(device_targets.values()))
                    secs_label = ', '.join(f"{s}s" for s in secs_shown)
                    yield prog(f"✅ Ext {ext_key}: ring time → {secs_label} [{schema}] — {msg}", "success")
                    written_count += 1
                else:
                    tag = f"[{schema}] " if schema else ""
                    yield prog(f"❌ Ext {ext_key}: {tag}{msg}", "error")
            except Exception as e:
                yield prog(f"❌ Ext {ext_key}: {str(e)}", "error")

        task_control.clear(task_id)
        yield json.dumps({"type": "complete", "cancelled": cancelled, "written": written_count}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


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
        _add_ring_dropdown(ws1, AUDIT_COLS)
        _autosize(ws1)

        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        _autosize(writer.sheets['Format Guide'])

    output.seek(0)
    return send_file(
        output,
        download_name="deskphone_ring_time_template.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# --- DEBUG ROUTE -----------------------------------------------------------
@deskphone_ring_time_bp.route('/debug', methods=['GET'])
@require_rc_token
def debug_ring_time():
    """Raw dump of an extension's devices, its default V2 state rule and V1
    answering rule, so the ring-time parser can be checked against the real
    schema. Visit e.g. /api/deskphone_ring_time/debug?ext=101 while signed in."""
    ext_num = (request.args.get('ext') or '').strip()
    if not ext_num:
        return jsonify({"error": "Pass ?ext=<extension number>, e.g. ?ext=101"}), 400

    out = {"ext_requested": ext_num}
    try:
        ext_id = get_extension_id(ext_num)
        if not ext_id:
            return jsonify({"error": f"Extension {ext_num} not found."}), 404
        out["ext_id"] = ext_id

        devices = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device",
                              return_response=True)
        out["devices"] = devices.json() if getattr(devices, 'ok', False) else {
            "status": getattr(devices, 'status_code', '?'), "body": getattr(devices, 'text', '')}

        v2_url = V2_STATE_RULE_BASE.format(ext_id=ext_id) + f"/{DEFAULT_STATE_RULE_ID}"
        v2 = rc_api_call(v2_url, return_response=True)
        out["v2_default_state_rule"] = v2.json() if getattr(v2, 'ok', False) else {
            "status": getattr(v2, 'status_code', '?'), "body": getattr(v2, 'text', '')}

        v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{DEFAULT_STATE_RULE_ID}"
        v1 = rc_api_call(v1_url, params={'view': 'Detailed'}, return_response=True)
        out["v1_default_answering_rule"] = v1.json() if getattr(v1, 'ok', False) else {
            "status": getattr(v1, 'status_code', '?'), "body": getattr(v1, 'text', '')}

        schema, durations, _ = get_default_ring_config(ext_id)
        out["parsed"] = {"schema": schema, "device_durations_secs": durations}
    except Exception as e:
        out["error"] = str(e)

    return current_app.response_class(json.dumps(out, indent=2), mimetype='application/json')
