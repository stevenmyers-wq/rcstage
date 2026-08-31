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
    build_v1_payload, format_phone, parse_rule_to_row, transform_v1_to_v2,
    fetch_all_extensions, fetch_sites, resolve_type_filter,
    extension_site_id, FILTER_GROUPS,
    get_existing_v2_greeting, get_existing_v1_conditions, get_existing_v2_conditions,
    fetch_v2_interaction_rules, THIS_EXTENSION,
    apply_phone_forward_to_dispatching, fac_daily_all_day_conditions,
)


class _MemFile:
    """Minimal in-memory stand-in for a Werkzeug FileStorage.

    Uploaded audio is read into memory up-front so the same clip can be applied
    to several rows (a FileStorage stream can only be read once). Exposes the
    small surface message_management.upload_custom_greeting relies on."""
    def __init__(self, filename, data, content_type):
        self.filename = filename
        self._data = data
        self.content_type = content_type or 'audio/wav'

    def read(self):
        return self._data


def _resp_id(resp):
    """Best-effort extraction of a created rule's id from a write response."""
    try:
        body = resp.json()
        return body.get('id') if isinstance(body, dict) else None
    except Exception:
        return None


# Dropdown (data-validation) specs shared by the template AND the audit export,
# so an audited sheet can be edited in place and re-uploaded without retyping.
_DROPDOWN_SPECS = {
    'Enabled': '"Yes,No"',
    'Action': '"Transfer to External,Transfer to Extension,Send to Voicemail,Play Message,Play Message and Disconnect,Fwd Direct To Main"',
    'Greeting': '"Default,Custom"',
}


def add_rule_dropdowns(ws, columns, max_row=1000):
    """Attaches the Enabled/Action/Greeting dropdowns to whichever columns hold
    those headers, resolving the column letter by header name so it works for
    both the template and the (wider) audit layout."""
    for name, formula in _DROPDOWN_SPECS.items():
        if name in columns:
            letter = get_column_letter(columns.index(name) + 1)
            dv = DataValidation(type="list", formula1=formula, allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{max_row}")

custom_rules_bp = Blueprint('custom_rules', __name__)
custom_rules_bp.add_url_rule('/api/custom_rules/cancel', 'custom_rules_cancel', task_control.cancel_view, methods=['POST'])

# --- HELPERS ---
def get_extension_id(extension_number):
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'): ext_num = ext_num[:-2]
    resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'extensionNumber': ext_num})
    if resp and 'records' in resp and len(resp['records']) > 0:
        return resp['records'][0]['id']
    return None

def get_user_devices(ext_id):
    try:
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/device')
        return resp.get('records', []) if resp else []
    except: return []

# --- FILTER OPTIONS ROUTE ---
@custom_rules_bp.route('/api/custom_rules/filters', methods=['GET'])
@require_rc_token
def custom_rules_filters():
    """Feeds the Audit scope UI: the available extension-type filters and the
    account's Sites (so the operator can scope the crawl by type and/or site)."""
    try:
        sites = fetch_sites()
        if not any(s.get('id') == 'main-site' for s in sites):
            sites.insert(0, {'id': 'main-site', 'name': 'Main Site'})
        return jsonify({'types': list(FILTER_GROUPS.keys()), 'sites': sites})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- DEBUG ROUTE ---
@custom_rules_bp.route('/api/custom_rules/debug', methods=['GET'])
@require_rc_token
def debug_rules():
    """Raw dump of an extension's rules exactly as RingCentral returns them, so
    the parser can be fixed against the real schema instead of assumptions.

    Visit in the browser (on the tool's own site, while signed in), e.g.
    /api/custom_rules/debug?ext=101 — returns the V1 answering-rule payload, the
    V2 interaction-rules list, and each V2 rule fetched by id (the authoritative
    body the audit parses)."""
    ext_num = (request.args.get('ext') or '').strip()
    if not ext_num:
        return jsonify({"error": "Pass ?ext=<extension number>, e.g. /api/custom_rules/debug?ext=101"}), 400

    out = {"ext_requested": ext_num}
    try:
        ext_id = get_extension_id(ext_num)
        if not ext_id:
            return jsonify({"error": f"Extension {ext_num} not found."}), 404
        out["ext_id"] = ext_id

        # V1 answering rules (Detailed)
        v1 = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule",
                         params={'view': 'Detailed'}, return_response=True)
        out["v1_answering_rules"] = v1.json() if getattr(v1, 'ok', False) else {
            "status": getattr(v1, 'status_code', '?'), "body": getattr(v1, 'text', '')}

        # V2 interaction-rules: the list as-is …
        base = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
        v2list = rc_api_call(base, return_response=True)
        if getattr(v2list, 'ok', False):
            list_body = v2list.json()
            out["v2_interaction_rules_list"] = list_body
            # … and each rule fetched by id (what the audit actually parses).
            detailed = []
            for rec in (list_body.get('records') or []):
                rid = rec.get('id')
                if not rid:
                    continue
                one = rc_api_call(f"{base}/{rid}", return_response=True)
                detailed.append(one.json() if getattr(one, 'ok', False) else {
                    "id": rid, "status": getattr(one, 'status_code', '?'),
                    "body": getattr(one, 'text', '')})
            out["v2_interaction_rules_detailed"] = detailed
        else:
            out["v2_interaction_rules_list"] = {
                "status": getattr(v2list, 'status_code', '?'), "body": getattr(v2list, 'text', '')}

        # Call-handling STATES + state-rules — to interpret a "States not found"
        # (CMN-102) rejection on the interaction-rules endpoint: does this
        # extension actually expose the comm-handling states the custom-rule
        # resource hangs off, or is it a bare/virtual extension without them?
        ch = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling"
        for label, url in (
            ("v2_states", f"{ch}/states"),
            ("v2_state_rules", f"{ch}/voice/state-rules"),
        ):
            r = rc_api_call(url, return_response=True)
            out[label] = r.json() if getattr(r, 'ok', False) else {
                "status": getattr(r, 'status_code', '?'), "body": getattr(r, 'text', '')}
    except Exception as e:
        out["error"] = str(e)

    return current_app.response_class(
        json.dumps(out, indent=2), mimetype='application/json')


# --- FORWARD ALL CALLS (STATE) ROUTE ---
@custom_rules_bp.route('/api/custom_rules/forward_all_calls', methods=['POST'])
@require_rc_token
@track_usage('Custom Rules Forward All Calls')
def forward_all_calls():
    """Turn on (or off) the extension's 'Forward All Calls' state, forwarding to
    an external number 24/7.

    Unlike custom/interaction rules, 'forward-all-calls' is a Default state that
    exists on every migrated user extension, so it does not hit the "States not
    found" wall. We read-modify-write the state RULE (set the phone target while
    preserving the existing voicemail/greeting defaults), then enable/disable the
    STATE. Returns a JSON log with the raw status codes of each RingCentral call
    so a failure is diagnosable without another round-trip."""
    data = request.get_json(silent=True) or {}
    raw_ext = str(data.get('ext') or '').strip()
    enable = bool(data.get('enabled', True))
    raw_number = str(data.get('number') or '').strip()

    steps = []

    def note(msg, ok=None):
        steps.append({"msg": msg, "ok": ok})

    if not raw_ext:
        return jsonify({"ok": False, "error": "Extension number is required."}), 400

    ext_id = get_extension_id(raw_ext)
    if not ext_id:
        return jsonify({"ok": False, "error": f"Extension {raw_ext} not found."}), 404

    ch = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling"
    rule_url = f"{ch}/voice/state-rules/forward-all-calls"
    state_url = f"{ch}/states/forward-all-calls"

    try:
        if enable:
            phone = format_phone(raw_number) if raw_number else None
            if not phone:
                return jsonify({"ok": False,
                                "error": "A valid destination number is required to enable forwarding."}), 400

            # 1) Read the current forward-all-calls rule so we can modify and
            #    re-send its complete dispatching (RingCentral requires the full
            #    object on PATCH, and this keeps its greeting/voicemail defaults).
            r = rc_api_call(rule_url, return_response=True)
            r_status = getattr(r, 'status_code', '?')
            dispatching = None
            if getattr(r, 'ok', False):
                try:
                    dispatching = (r.json() or {}).get('dispatching')
                except Exception:
                    dispatching = None
                note(f"GET forward-all-calls rule [{r_status}]", True)
            else:
                # Fall back to a built-from-scratch dispatching; the PATCH below
                # will still tell us if the state genuinely isn't available.
                note(f"GET forward-all-calls rule [{r_status}] — building dispatching from scratch",
                     False)

            dispatching = apply_phone_forward_to_dispatching(dispatching, phone,
                                                             target_name=f"Forward {phone}")

            # 2) Write the rule (set the forward-to-number target).
            pr = rc_api_call(rule_url, method='PATCH', json={"dispatching": dispatching},
                             return_response=True)
            pr_status = getattr(pr, 'status_code', '?')
            if not getattr(pr, 'ok', False):
                body = ((getattr(pr, 'text', '') or '').strip())[:400]
                note(f"PATCH forward-all-calls rule [{pr_status}] {body}", False)
                return jsonify({"ok": False, "ext_id": ext_id, "steps": steps,
                                "error": f"Could not set the forward target (HTTP {pr_status})."}), 502
            note(f"PATCH forward-all-calls rule [{pr_status}]", True)

            # 3) Enable the state with a 24/7 daily schedule.
            ps = rc_api_call(state_url, method='PATCH',
                             json={"enabled": True, "conditions": fac_daily_all_day_conditions()},
                             return_response=True)
            ps_status = getattr(ps, 'status_code', '?')
            if not getattr(ps, 'ok', False):
                body = ((getattr(ps, 'text', '') or '').strip())[:400]
                note(f"PATCH forward-all-calls state (enable) [{ps_status}] {body}", False)
                return jsonify({"ok": False, "ext_id": ext_id, "steps": steps,
                                "error": f"Set the target but could not enable the state (HTTP {ps_status})."}), 502
            note(f"PATCH forward-all-calls state (enable) [{ps_status}]", True)

            return jsonify({"ok": True, "ext_id": ext_id, "steps": steps,
                            "message": f"Ext {raw_ext}: forwarding all calls to {phone} (24/7)."})

        # Disable: just turn the state off; leave the configured target in place.
        ps = rc_api_call(state_url, method='PATCH', json={"enabled": False},
                         return_response=True)
        ps_status = getattr(ps, 'status_code', '?')
        if not getattr(ps, 'ok', False):
            body = ((getattr(ps, 'text', '') or '').strip())[:400]
            note(f"PATCH forward-all-calls state (disable) [{ps_status}] {body}", False)
            return jsonify({"ok": False, "ext_id": ext_id, "steps": steps,
                            "error": f"Could not disable the state (HTTP {ps_status})."}), 502
        note(f"PATCH forward-all-calls state (disable) [{ps_status}]", True)
        return jsonify({"ok": True, "ext_id": ext_id, "steps": steps,
                        "message": f"Ext {raw_ext}: Forward All Calls turned off."})

    except Exception as e:
        note(f"Exception: {e}", False)
        return jsonify({"ok": False, "ext_id": ext_id, "steps": steps, "error": str(e)}), 500


# --- AUDIT ROUTE ---
@custom_rules_bp.route('/api/custom_rules/audit', methods=['GET'])
@require_rc_token
@track_usage('Custom Rules Audit')
def audit_rules():
    # Scope filters (all optional). `type` may be repeated; `site` is a Site id.
    # With nothing selected we audit every rule-capable extension (Users, Call
    # Queues, IVRs, groups, etc.) across every site. Read request args up-front
    # so the streaming generator doesn't depend on them mid-flight.
    allowed_types = resolve_type_filter(request.args.getlist('type'))
    site_filter = (request.args.get('site') or '').strip()

    cols = ['Ext Number', 'Ext Name', 'Type', 'Site', 'Rule ID', 'Rule Name', 'Enabled',
            'Caller ID', 'Called Number',
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'Specific Dates',
            'Action', 'External Number', 'Transfer Extension', 'Voicemail Recipient',
            'Greeting', 'Greeting File']

    def generate():
        try:
            yield json.dumps({"type": "status", "message": "Loading account directory…"}) + "\n"
            all_extensions = fetch_all_extensions()
            if not all_extensions:
                yield json.dumps({"type": "error", "message": "Failed to fetch extensions list"}) + "\n"
                return

            # Lookup over the FULL directory (any type) so transfer/voicemail
            # targets resolve to an extension number even when the target's type
            # is outside the audit scope.
            ext_lookup = {str(e['id']): e for e in all_extensions if e.get('id')}

            targets = [e for e in all_extensions
                       if (e.get('type') or '') in allowed_types and e.get('status') != 'Disabled']
            if site_filter:
                targets = [e for e in targets if extension_site_id(e) == site_filter]

            total = len(targets)
            yield json.dumps({"type": "start", "total": total}) + "\n"

            audit_data = []
            for i, ext in enumerate(targets):
                ext_id = ext['id']
                rules_found = False

                # Try V2. Each rule is fetched by id so its full inline
                # `dispatching` is parsed (the collection list only carries a
                # `dispatchingRef`, which would leave every action column blank).
                try:
                    v2_rules = fetch_v2_interaction_rules(ext_id)
                except Exception:
                    v2_rules = None  # endpoint failure (e.g. V1 account) -> V1 fallback

                # A non-empty V2 result means V2 owns this extension; an empty
                # list must not suppress the V1 fallback. Parse each rule under
                # its OWN guard so one rule with an unexpected shape can't drop
                # the others (or, since V1 is 403 on new-call-handling accounts,
                # wipe the whole extension) — a failing rule becomes a visible
                # diagnostic row instead.
                if v2_rules:
                    rules_found = True
                    for rule in v2_rules:
                        try:
                            audit_data.append(parse_rule_to_row(ext, rule, is_v2=True, ext_lookup=ext_lookup))
                        except Exception as pe:
                            audit_data.append({
                                'Ext Number': ext.get('extensionNumber'),
                                'Ext Name': ext.get('name'),
                                'Rule ID': rule.get('id'),
                                'Rule Name': rule.get('displayName') or rule.get('name'),
                                'Action': f'⚠️ Could not parse rule: {pe}',
                            })

                # Fallback V1
                if not rules_found:
                    try:
                        v1_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', params={'view': 'Detailed'})
                        if v1_resp and 'records' in v1_resp:
                            for rule in v1_resp['records']:
                                if rule.get('type') == 'Custom':
                                    audit_data.append(parse_rule_to_row(ext, rule, is_v2=False, ext_lookup=ext_lookup))
                    except Exception:
                        pass

                yield json.dumps({
                    "type": "progress", "current": i + 1, "total": total,
                    "message": str(ext.get('extensionNumber') or ext_id)
                }) + "\n"

            if not audit_data:
                audit_data = [{'Ext Number': 'No Data', 'Rule Name': 'No Custom Rules Found'}]

            df = pd.DataFrame(audit_data)
            for c in cols:
                if c not in df.columns: df[c] = ''
            df = df[cols]

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Audit')
                worksheet = writer.sheets['Audit']
                # Same dropdowns as the blank template, so the audit can be
                # edited in place and re-uploaded without retyping values.
                add_rule_dropdowns(worksheet, cols)
                for column in worksheet.columns:
                    length = max(len(str(cell.value) or "") for cell in column)
                    worksheet.column_dimensions[column[0].column_letter].width = length + 5

            output.seek(0)
            file_b64 = base64.b64encode(output.read()).decode('ascii')
            filename = f"Rule_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx"
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

# --- UPDATE ROUTE ---
@custom_rules_bp.route('/api/update_rules', methods=['POST'])
@require_rc_token
@track_usage('Custom Rules Update')
def update_rules():
    if 'file' not in request.files: return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    task_id = request.form.get('task_id')
    try:
        if file.filename.endswith('.csv'): df = pd.read_csv(file)
        else: df = pd.read_excel(file, sheet_name=(request.form.get('sheet_name') or 0))
        df.columns = df.columns.str.strip()
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    # Fail loudly if the sheet doesn't have the key column, instead of silently
    # treating every row as blank. Guards against uploading the wrong sheet or a
    # renamed header.
    if 'Ext Number' not in df.columns:
        found = ', '.join(str(c) for c in df.columns) or '(none)'
        return jsonify({"error": f"The sheet has no 'Ext Number' column. Found columns: {found}. "
                                 f"Make sure you're uploading the 'Template' sheet or an Audit export."}), 400

    # Read any uploaded greeting audio into memory now (request context), keyed
    # by lowercased filename so a 'Greeting File' cell can reference it. The
    # spreadsheet itself is under the 'file' key and is skipped here.
    uploaded_audio = {}
    for key in request.files:
        if key == 'file':
            continue
        for fs in request.files.getlist(key):
            if fs and fs.filename:
                uploaded_audio[fs.filename.strip().lower()] = _MemFile(
                    fs.filename, fs.read(), getattr(fs, 'content_type', 'audio/wav'))

    total = len(df)

    def apply_greeting_audio(ext_id, rule_id, action_type, mem_file):
        # Reuse message_management's proven multipart greeting uploader. Imported
        # lazily so this module doesn't pull in its heavy (TTS) dependencies
        # unless an audio upload is actually requested.
        from webapp.message_management.utils import upload_custom_greeting
        slot = 'Announcement' if action_type == 'PlayAnnouncementOnly' else 'Voicemail'
        upload_custom_greeting(ext_id, mem_file, f"{rule_id}:{slot}")

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

        for index, row in df.iterrows():
            # Cooperative stop: rules already written stand; the rest are skipped.
            if task_control.is_stopped(task_id):
                cancelled = True
                current = total
                yield prog("■ Stopped by user — remaining rows were skipped.", "info")
                break

            current += 1
            raw_ext_num = row.get('Ext Number')
            if pd.isna(raw_ext_num):
                # A row that carries other data but no Ext Number is a mistake
                # worth flagging; a wholly empty row (trailing blanks) is silent.
                if row.notna().any():
                    yield prog(f"Row {index}: ⚠️ Skipped — 'Ext Number' is blank.", "info")
                else:
                    yield prog()
                continue

            try:
                ext_id = get_extension_id(raw_ext_num)
                if not ext_id:
                    yield prog(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.", "error")
                    continue

                user_devices = get_user_devices(ext_id)
                payload, action_type = build_v1_payload(row, ext_id)

                rule_id = str(row.get('Rule ID')).replace('.0', '').strip() if pd.notna(row.get('Rule ID')) else ""
                is_update = bool(rule_id)

                # Conditions (caller / called number / schedule) are required to
                # CREATE a rule, but an UPDATE may only be changing the action or
                # greeting — so carry the existing rule's conditions forward
                # instead of blanking them, and never block the edit.
                if not any(k in payload for k in ['callers', 'calledNumbers', 'schedule']):
                    if is_update:
                        payload.update(get_existing_v1_conditions(ext_id, rule_id))
                    else:
                        yield prog(f"⚠️ Ext {raw_ext_num}: Skipped - No conditions found (required to create a NEW rule).", "info")
                        continue

                # Greeting intent for this row (used on the V2 path + audio upload).
                # Resolve which uploaded clip (if any) to apply:
                #   - a named 'Greeting File' picks that clip;
                #   - Greeting='Custom' with no file named falls back to the sole
                #     uploaded clip (the common "one rule, one file" case), so a
                #     forgotten filename no longer silently applies nothing.
                greeting_choice = str(row.get('Greeting') or '').strip().lower()
                greeting_file_name = str(row.get('Greeting File') or '').strip().lower()
                audio_to_apply = None
                audio_note = None
                if greeting_file_name:
                    if greeting_file_name in uploaded_audio:
                        audio_to_apply = uploaded_audio[greeting_file_name]
                    else:
                        audio_note = f"greeting file '{greeting_file_name}' was not among the uploaded audio files"
                elif greeting_choice == 'custom' and uploaded_audio:
                    if len(uploaded_audio) == 1:
                        audio_to_apply = next(iter(uploaded_audio.values()))
                    else:
                        audio_note = "Greeting is 'Custom' but no 'Greeting File' named — add the filename to that column (multiple audio files were uploaded)"
                has_audio = audio_to_apply is not None

                if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
                    raw_ph = str(row.get('External Number')).strip()
                    payload['unconditionalForwarding'] = {'phoneNumber': format_phone(raw_ph)}
                elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
                    target_id = get_extension_id(row.get('Transfer Extension'))
                    if target_id: payload['transfer'] = {'extension': {'id': target_id}}
                    else:
                        yield prog(f"⚠️ Target Ext {row.get('Transfer Extension')} not found.", "error")
                        continue
                elif action_type == 'TakeMessagesOnly':
                    # Blank or a This/Self keyword -> the owning extension's own
                    # mailbox (RingCentral's "This extension"); a number -> that
                    # extension ("Another extension").
                    vm_raw = row.get('Voicemail Recipient')
                    recipient_id = ext_id
                    if pd.notna(vm_raw):
                        vv = str(vm_raw).strip().lower()
                        if vv in ('', 'this', 'self', 'this ext', THIS_EXTENSION.lower()):
                            recipient_id = ext_id
                        else:
                            vm_id = get_extension_id(vm_raw)
                            recipient_id = vm_id if vm_id else ext_id
                    payload['voicemail'] = {'recipient': {'id': recipient_id}}

                v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
                if is_update: v1_url += f"/{rule_id}"
                v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
                if is_update: v2_url += f"/{rule_id}"

                method = "PUT" if is_update else "POST"

                written_ok = False
                written_rule_id = rule_id or None

                # Inspect the raw response rather than relying on exception types:
                # rc_api_call re-wraps HTTP errors into a generic Exception, so a
                # `except HTTPError` never fires. Any V1 rejection falls through
                # to the V2 (new call-handling) model that Call Queues, Sites and
                # newer rules require.
                v1_resp = rc_api_call(v1_url, method=method, json=payload, return_response=True)
                if v1_resp is not None and getattr(v1_resp, 'ok', False):
                    written_rule_id = written_rule_id or _resp_id(v1_resp)
                    yield prog(f"✅ {method} Rule Ext {raw_ext_num} (V1)", "success")
                    written_ok = True
                else:
                    v1_status = getattr(v1_resp, 'status_code', '?')
                    v1_body = ((getattr(v1_resp, 'text', '') or '').strip())[:300]
                    try:
                        # Greeting for the V2 rule body: an uploaded clip is applied
                        # separately after the write (use Default in the body); an
                        # explicit 'Default' forces Default; otherwise preserve the
                        # rule's existing greeting so we never silently replace it.
                        if has_audio or greeting_choice == 'default':
                            vm_greeting = {"effectiveGreetingType": "Default"}
                        else:
                            vm_greeting = get_existing_v2_greeting(ext_id, rule_id)
                        v2_payload = transform_v1_to_v2(payload, ext_id, user_devices, vm_greeting=vm_greeting)
                        # On a V2 update with no conditions in the sheet, keep the
                        # rule's existing conditions rather than replacing them with
                        # an empty array.
                        if is_update and not v2_payload.get('conditions'):
                            existing_conditions = get_existing_v2_conditions(ext_id, rule_id)
                            if existing_conditions:
                                v2_payload['conditions'] = existing_conditions
                        # V2 requires PUT for updating existing rules, or POST for new
                        v2_method = "PUT" if is_update else "POST"
                        # Prime the extension's comm-handling before creating the
                        # first custom rule. On an extension that has never had a
                        # custom rule, the interaction-rules endpoint 404s ("States
                        # not found") until its call-handling states are read/
                        # materialised — mirroring the admin UI, which GETs
                        # state-rules and interaction-rules before it POSTs.
                        # Capture the primer status codes so the debug output shows
                        # (a) that the GETs actually ran and (b) whether the states
                        # resource itself answers 200 or is the thing 404-ing.
                        prime_log = ""
                        if not is_update:
                            ch = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling"
                            sr = rc_api_call(f"{ch}/voice/state-rules", return_response=True)
                            ir = rc_api_call(f"{ch}/voice/interaction-rules", return_response=True)
                            prime_log = (
                                f"GET state-rules [{getattr(sr, 'status_code', '?')}] "
                                f"GET interaction-rules [{getattr(ir, 'status_code', '?')}]"
                            )
                        v2_resp = rc_api_call(v2_url, method=v2_method, json=v2_payload, return_response=True)
                        if v2_resp is not None and getattr(v2_resp, 'ok', False):
                            written_rule_id = written_rule_id or _resp_id(v2_resp)
                            prime_note = f" ({prime_log})" if prime_log else ""
                            yield prog(f"✅ {v2_method} Rule Ext {raw_ext_num} (V2){prime_note}", "success")
                            written_ok = True
                        else:
                            v2_status = getattr(v2_resp, 'status_code', '?')
                            v2_body = ((getattr(v2_resp, 'text', '') or '').strip())[:400]
                            # Echo the exact payload we sent so a failure can be
                            # diagnosed against a known-good rule without another
                            # round-trip (the response body only names the offending
                            # parameter, not the value we sent for it).
                            try:
                                v2_sent = json.dumps(v2_payload)
                            except Exception:
                                v2_sent = str(v2_payload)
                            prime_note = f"{prime_log} · " if prime_log else ""
                            yield prog(
                                f"❌ Ext {raw_ext_num}: write failed. "
                                f"V1 [{v1_status}] {v1_body or '(no body)'} · "
                                f"{prime_note}"
                                f"V2 {v2_method} {v2_url} [{v2_status}] {v2_body or '(no body)'} · "
                                f"V2 sent: {v2_sent}", "error")
                    except Exception as v2_err:
                        yield prog(f"❌ V2 Error Ext {raw_ext_num}: {str(v2_err)}", "error")

                # Post-write: apply an uploaded greeting clip to the rule.
                if has_audio:
                    if not written_ok or not written_rule_id:
                        yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: rule not written, skipped greeting audio.", "info")
                    elif action_type not in ('TakeMessagesOnly', 'PlayAnnouncementOnly'):
                        yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: greeting audio ignored (Action isn't Send to Voicemail / Play Message).", "info")
                    else:
                        try:
                            apply_greeting_audio(ext_id, written_rule_id, action_type, audio_to_apply)
                            yield prog(f"   ↳ 🎵 Applied greeting '{audio_to_apply.filename}' to Ext {raw_ext_num}", "success")
                        except Exception as ge:
                            yield prog(f"   ↳ ❌ Greeting upload failed for Ext {raw_ext_num}: {str(ge)}", "error")
                elif audio_note:
                    yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: {audio_note}.", "info")

                if written_ok:
                    written_count += 1
            except Exception as e:
                yield prog(f"❌ Error Ext {raw_ext_num}: {str(e)}", "error")

        task_control.clear(task_id)
        yield json.dumps({"type": "complete", "cancelled": cancelled, "written": written_count}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

# --- TEMPLATE DOWNLOAD ROUTE (UPDATED) ---
@custom_rules_bp.route('/api/custom_rules/template', methods=['GET'])
def download_template():
    # 1. Define Template Columns
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled',
        'Caller ID', 'Called Number',
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
        'Specific Dates',
        'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient',
        'Greeting', 'Greeting File'
    ]

    # 2. Define Instructions / Examples
    instructions_data = [
        {"Field": "Ext Number", "Required": "Yes", "Format": "101", "Notes": "The extension the rule belongs to (User, Call Queue, IVR, etc.)."},
        {"Field": "Rule ID", "Required": "No", "Format": "123456", "Notes": "Leave BLANK to create a NEW rule. Fill to UPDATE an existing rule."},
        {"Field": "Caller ID", "Required": "No", "Format": "+61400123456", "Notes": "Incoming numbers to match. Comma-separated."},
        {"Field": "Called Number", "Required": "No", "Format": "+61299990000", "Notes": "The DID the caller dialed. Comma-separated."},
        {"Field": "Days (Mon-Sun)", "Required": "No", "Format": "9:00 AM - 5:00 PM", "Notes": "12-hour format with AM/PM. Separate multiple ranges with commas: '9:00 AM - 12:00 PM, 1:00 PM - 5:00 PM'"},
        {"Field": "Specific Dates", "Required": "No", "Format": "YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM", "Notes": "Example: '2024-12-25 00:00 to 2024-12-26 23:59'. Separate multiple date ranges with commas."},
        {"Field": "Action", "Required": "Yes", "Format": "Select from Dropdown", "Notes": "Use the dropdown box provided in the Template sheet."},
        {"Field": "External Number", "Required": "If Action=Transfer", "Format": "+614...", "Notes": "E.164 format preferred."},
        {"Field": "Voicemail Recipient", "Required": "No", "Format": "This extension / 1001", "Notes": "For 'Send to Voicemail'. 'This extension' (or blank) uses the extension's own mailbox; an extension number sends to Another extension."},
        {"Field": "Greeting", "Required": "No", "Format": "Select from Dropdown", "Notes": "For voicemail/announcement actions. 'Default' = system greeting; 'Custom' = keep the existing custom greeting (not overwritten). Leave blank to keep as-is."},
        {"Field": "Greeting File", "Required": "No", "Format": "welcome.wav", "Notes": "Optional. Name of an audio file you also upload with the sheet; it is applied as the rule's custom greeting after the rule is written. .wav or .mp3."},
        {"Field": "Enabled", "Required": "No", "Format": "Select from Dropdown", "Notes": "Defaults to Yes."}
    ]

    df_template = pd.DataFrame([], columns=columns)
    df_instructions = pd.DataFrame(instructions_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Template
        df_template.to_excel(writer, index=False, sheet_name='Template')
        ws1 = writer.sheets['Template']
        
        # --- Add Dropdown Validations (Enabled / Action / Greeting) ---
        add_rule_dropdowns(ws1, columns)

        # Auto-adjust column widths
        for column in ws1.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            ws1.column_dimensions[column[0].column_letter].width = length + 5

        # Sheet 2: Format Guide
        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        ws2 = writer.sheets['Format Guide']
        for column in ws2.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            ws2.column_dimensions[column[0].column_letter].width = length + 10

    output.seek(0)
    return send_file(
        output, 
        download_name="custom_rules_template.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
