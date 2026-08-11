import io
import json
import base64
import pandas as pd
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from webapp import task_control
from .utils import (
    build_v1_payload, format_phone, parse_rule_to_row, transform_v1_to_v2,
    fetch_all_extensions, fetch_sites, resolve_type_filter,
    extension_site_id, FILTER_GROUPS,
    get_existing_v2_greeting, THIS_EXTENSION,
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

                # Try V2
                try:
                    v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
                    v2_resp = rc_api_call(v2_url, raise_error=True)
                    # Only treat V2 as authoritative when it actually returns
                    # rules; an empty list must not suppress the V1 fallback.
                    if v2_resp and v2_resp.get('records'):
                        for rule in v2_resp['records']:
                            audit_data.append(parse_rule_to_row(ext, rule, is_v2=True, ext_lookup=ext_lookup))
                        rules_found = True
                except Exception:
                    pass

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
        else: df = pd.read_excel(file)
        df.columns = df.columns.str.strip()
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

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
                yield prog()  # advance the bar for a blank row, no log line
                continue

            try:
                ext_id = get_extension_id(raw_ext_num)
                if not ext_id:
                    yield prog(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.", "error")
                    continue

                user_devices = get_user_devices(ext_id)
                payload, action_type = build_v1_payload(row, ext_id)

                if not any(k in payload for k in ['callers', 'calledNumbers', 'schedule']):
                    yield prog(f"⚠️ Ext {raw_ext_num}: Skipped - No conditions found.", "info")
                    continue

                # Greeting intent for this row (used on the V2 path + audio upload).
                greeting_choice = str(row.get('Greeting') or '').strip().lower()
                greeting_file_name = str(row.get('Greeting File') or '').strip().lower()
                has_audio = bool(greeting_file_name) and greeting_file_name in uploaded_audio

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

                rule_id = str(row.get('Rule ID')).replace('.0', '').strip() if pd.notna(row.get('Rule ID')) else ""
                is_update = bool(rule_id)

                v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
                if is_update: v1_url += f"/{rule_id}"
                v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
                if is_update: v2_url += f"/{rule_id}"

                method = "PUT" if is_update else "POST"

                written_ok = False
                written_rule_id = rule_id or None

                try:
                    resp = rc_api_call(v1_url, method=method, json=payload, raise_error=True)
                    if not written_rule_id and isinstance(resp, dict):
                        written_rule_id = resp.get('id')
                    yield prog(f"✅ {method} Rule Ext {raw_ext_num} (V1)", "success")
                    written_ok = True
                except requests.exceptions.HTTPError as http_err:
                    if "NewCallHandlingAndForwarding" in http_err.response.text:
                        try:
                            # Greeting for the V2 rule body: an uploaded clip is
                            # applied separately after the write (use Default in
                            # the body); an explicit 'Default' forces Default;
                            # otherwise preserve the rule's existing greeting so
                            # we never silently replace a custom one.
                            if has_audio or greeting_choice == 'default':
                                vm_greeting = {"effectiveGreetingType": "Default"}
                            else:
                                vm_greeting = get_existing_v2_greeting(ext_id, rule_id)
                            v2_payload = transform_v1_to_v2(payload, ext_id, user_devices, vm_greeting=vm_greeting)
                            # V2 requires PUT for updating existing rules, or POST for new
                            v2_method = "PUT" if is_update else "POST"
                            resp2 = rc_api_call(v2_url, method=v2_method, json=v2_payload, raise_error=True)
                            if not written_rule_id and isinstance(resp2, dict):
                                written_rule_id = resp2.get('id')
                            yield prog(f"✅ {v2_method} Rule Ext {raw_ext_num} (V2)", "success")
                            written_ok = True
                        except Exception as v2_err:
                            error_msg = str(v2_err)
                            if hasattr(v2_err, 'response') and v2_err.response is not None:
                                error_msg += f" | Details: {v2_err.response.text}"
                            yield prog(f"❌ V2 Error Ext {raw_ext_num}: {error_msg}", "error")
                    else:
                        raise http_err

                # Post-write: apply an uploaded greeting clip to the rule.
                if has_audio:
                    if not written_ok or not written_rule_id:
                        yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: rule not written, skipped greeting audio.", "info")
                    elif action_type not in ('TakeMessagesOnly', 'PlayAnnouncementOnly'):
                        yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: 'Greeting File' ignored (Action isn't Send to Voicemail / Play Message).", "info")
                    else:
                        try:
                            apply_greeting_audio(ext_id, written_rule_id, action_type, uploaded_audio[greeting_file_name])
                            yield prog(f"   ↳ 🎵 Applied greeting '{uploaded_audio[greeting_file_name].filename}' to Ext {raw_ext_num}", "success")
                        except Exception as ge:
                            yield prog(f"   ↳ ❌ Greeting upload failed for Ext {raw_ext_num}: {str(ge)}", "error")
                elif greeting_file_name:
                    yield prog(f"   ↳ ⚠️ Ext {raw_ext_num}: greeting file '{greeting_file_name}' was not among the uploaded audio files.", "info")
            except Exception as e:
                yield prog(f"❌ Error Ext {raw_ext_num}: {str(e)}", "error")

        task_control.clear(task_id)
        yield json.dumps({"type": "complete", "cancelled": cancelled}) + "\n"

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
        
        # --- Add Dropdown Validations ---
        # Column E is Enabled, Column P is Action, Column T is Greeting.
        dv_enabled = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
        dv_action = DataValidation(type="list", formula1='"Transfer to External,Transfer to Extension,Send to Voicemail,Play Message,Play Message and Disconnect,Fwd Direct To Main"', allow_blank=True)
        dv_greeting = DataValidation(type="list", formula1='"Default,Custom"', allow_blank=True)

        ws1.add_data_validation(dv_enabled)
        ws1.add_data_validation(dv_action)
        ws1.add_data_validation(dv_greeting)

        # Apply to a generous range of rows (Row 2 to 1000)
        dv_enabled.add("E2:E1000")
        dv_action.add("P2:P1000")
        dv_greeting.add("T2:T1000")

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
