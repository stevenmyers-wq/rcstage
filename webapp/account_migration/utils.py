import io
import json
import zipfile
import threading
import contextvars
import logging
import pandas as pd
import time
import requests
from webapp.rc_api import rc_api_call
from webapp import task_control

logger = logging.getLogger(__name__)

migration_progress_store = {}


# ---------------------------------------------------------------------------
# Self-healing auth for long-running migration jobs (export / audit / import).
#
# Every RC call goes through safe_rc_api_call with an explicit token captured
# once at request time. On a large account the job runs long enough for the SM
# bridge token to expire mid-run — previously that surfaced as a hard failure
# (or silently dropped data). A background job now installs a _MigCaller in this
# ContextVar; safe_rc_api_call then uses its live token and re-mints the bridge
# in place on a 401, serialised behind a lock so concurrent work never mints two
# bridges at once (RingCentral rotates refresh tokens, so a double refresh would
# invalidate the other).
# ---------------------------------------------------------------------------
_mig_ctx = contextvars.ContextVar("account_migration_auth", default=None)


class _MigCaller:
    def __init__(self, auth_data):
        self.auth = auth_data
        self._lock = threading.Lock()

    @property
    def token(self):
        return self.auth.get("access_token")

    def heal(self, used_token):
        """Re-mint the bridge token in place. De-duplicated: if another thread
        already refreshed while we waited for the lock, reuse that token."""
        with self._lock:
            if self.auth.get("access_token") != used_token:
                return True
            try:
                from webapp.deskphone_ring_time.utils import _heal_bg_token
                return bool(_heal_bg_token(self.auth))
            except Exception:
                logger.exception("[account_migration] token heal failed")
                return False


def _mig_token(fallback=None):
    """Live bridge token from the active job, or the caller's fallback token."""
    caller = _mig_ctx.get()
    return caller.token if caller is not None else fallback


def _stopped_and_marked(task_id):
    """True if the user has asked to stop this import. When it returns True it
    also records a 'cancelled' status so the polling UI can react. Objects
    already created on the account remain; not-yet-created ones are skipped."""
    if not task_control.is_stopped(task_id):
        return False
    entry = migration_progress_store.get(task_id, {})
    update_progress(
        task_id, entry.get('current', 0), entry.get('total', 100),
        "Stopped by user. Objects already created remain; the rest were skipped.",
        status='cancelled'
    )
    return True

def update_progress(task_id, current, total, message, status='running'):
    # Update in place so accumulated per-item results AND any extra keys set by
    # the background runners (account_name, file_data, download_name, kind, …)
    # survive across progress updates.
    entry = migration_progress_store.get(task_id)
    if entry is None:
        entry = {'results': []}
        migration_progress_store[task_id] = entry
    entry['current'] = current
    entry['total'] = total
    entry['message'] = message
    entry['status'] = status
    entry.setdefault('results', [])

def add_result(task_id, category, item, status, detail=''):
    """Append a per-item outcome to the downloadable result listing."""
    entry = migration_progress_store.get(task_id)
    if entry is None:
        return
    entry.setdefault('results', []).append({
        'Category': category,
        'Item': item,
        'Status': status,
        'Detail': detail
    })

def safe_rc_api_call(endpoint, task_id=None, method='GET', token=None, json_payload=None, data=None, files=None, params=None, raise_error=True):
    """Wrapper around rc_api_call that explicitly handles 429 Rate Limits and 403s."""
    max_retries = 20
    healed = False  # allow one bridge re-mint per call on a 401

    for attempt in range(max_retries):
        # In a background job the live (self-healing) token wins over the token
        # captured at request time; outside a job this is just the passed token.
        call_token = _mig_token(fallback=token)
        resp = rc_api_call(
            endpoint,
            method=method,
            token=call_token,
            json=json_payload,
            data=data,
            files=files,
            params=params,
            return_response=True
        )

        status_code = getattr(resp, 'status_code', 500)

        # 401 → the bridge token expired mid-run. Re-mint once and retry.
        caller = _mig_ctx.get()
        if status_code == 401 and caller is not None and not healed:
            healed = True
            if caller.heal(call_token):
                if task_id and task_id in migration_progress_store:
                    entry = migration_progress_store[task_id]
                    clean = entry['message'].split(" (⏳")[0]
                    update_progress(task_id, entry['current'], entry['total'],
                                    f"{clean} (⏳ bridge refreshed — resuming…)")
                continue
            # heal failed → fall through to normal error handling below

        # Explicit 429 Handling
        if status_code == 429:
            retry_after = 60
            if hasattr(resp, 'headers') and 'Retry-After' in resp.headers:
                try:
                    retry_after = max(60, int(resp.headers['Retry-After']))
                except Exception:
                    pass
            
            msg = f"Rate limit hit! Pausing for {retry_after}s..."
            print(f"[RATE LIMIT] 429 on {endpoint}. {msg}")
            
            if task_id and task_id in migration_progress_store:
                current_msg = migration_progress_store[task_id]['message']
                clean_msg = current_msg.split(" (⏳")[0]
                update_progress(task_id, migration_progress_store[task_id]['current'], migration_progress_store[task_id]['total'], f"{clean_msg} (⏳ {msg})")
            
            time.sleep(retry_after + 2) 
            continue 

        # 403 Forbidden Handling (Unsupported ext types or permissions)
        if status_code == 403 or status_code == 404:
            return None
            
        # Success Handling
        if 200 <= status_code < 300:
            if status_code == 204:
                return {"success": True}
            try:
                return resp.json()
            except Exception:
                return {"success": True}
            
        # Error Handling
        if raise_error:
            error_body = resp.text if hasattr(resp, 'text') else str(resp)
            raise Exception(f"HTTP {status_code} on {method} {endpoint}: {error_body}")
        
        try:
            return resp.json()
        except Exception:
            return None
            
    raise Exception(f"Max retries exhausted due to rate limits on {endpoint}")

def download_audio_content(audio_uri, token, task_id=None):
    # Prefer the live (self-healing) job token so a long export whose bridge was
    # re-minted mid-run keeps fetching audio with the fresh token.
    headers = {"Authorization": f"Bearer {_mig_token(fallback=token)}"}
    
    for _ in range(10):
        response = requests.get(audio_uri, headers=headers)
        
        if response.status_code == 429:
            retry_after = 60
            if 'Retry-After' in response.headers:
                try:
                    retry_after = max(60, int(response.headers['Retry-After']))
                except Exception:
                    pass
                    
            msg = f"Rate limit hit! Pausing for {retry_after}s..."
            print(f"[RATE LIMIT] 429 on Audio DL. {msg}")
            
            if task_id and task_id in migration_progress_store:
                current_msg = migration_progress_store[task_id]['message']
                clean_msg = current_msg.split(" (⏳")[0]
                update_progress(task_id, migration_progress_store[task_id]['current'], migration_progress_store[task_id]['total'], f"{clean_msg} (⏳ {msg})")
                
            time.sleep(retry_after + 2)
            continue
        
        if response.status_code == 200:
            return response.content, response.headers.get('Content-Type', 'audio/mpeg')
        return None, None
        
    return None, None

def fetch_all_pages(endpoint, token=None, task_id=None):
    all_records = []
    page = 1
    while True:
        params = {'perPage': 500, 'page': page}
        resp = safe_rc_api_call(endpoint, task_id=task_id, params=params, method='GET', token=token, raise_error=False)
        if not resp or 'records' not in resp:
            break
        all_records.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.1)
    return all_records

# --- SHARED READ-ONLY COLLECTION ---
def _collect_config_data(task_id, token=None, zip_file=None, download_audio=True):
    """Perform the full read-only tenant scan shared by the migration export and
    the reader-friendly audit.

    When ``zip_file`` is provided and ``download_audio`` is True (the export
    path), custom greeting / IVR prompt audio is downloaded and written into the
    archive, and each entry in ``custom_audio_map`` carries its ``filename``.
    Otherwise (the audit path) only the audio metadata is recorded — enough to
    list which greetings are custom — without pulling the bytes."""
    update_progress(task_id, 2, 100, "Fetching Global Account Structure...")

    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token, task_id)
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token, task_id)
    sites = fetch_all_pages('/restapi/v1.0/account/~/sites', token, task_id)

    # Emergency Response Locations (ERLs). The list resource usually carries the
    # full location, but read the single-resource detail so the true (possibly
    # international) address shape and any detail-only fields round-trip on import.
    emergency_locations = []
    for erl in fetch_all_pages('/restapi/v1.0/account/~/emergency-locations', token, task_id):
        loc_id = erl.get('id')
        detail = None
        if loc_id:
            detail = safe_rc_api_call(f'/restapi/v1.0/account/~/emergency-locations/{loc_id}', task_id=task_id, method='GET', token=token, raise_error=False)
        emergency_locations.append(detail or erl)

    cost_centers = []
    try:
        cc_resp = safe_rc_api_call('/restapi/v1.0/account/~/cost-center', task_id=task_id, method='GET', token=token, raise_error=False)
        if cc_resp and 'records' in cc_resp:
            cost_centers = cc_resp['records']
    except Exception:
        pass

    templates = fetch_all_pages('/restapi/v1.0/account/~/templates', token, task_id)
    custom_roles = fetch_all_pages('/restapi/v1.0/account/~/custom-roles', token, task_id)
    call_recording = safe_rc_api_call('/restapi/v1.0/account/~/call-recording', task_id=task_id, method='GET', token=token, raise_error=False)
    company_business_hours = safe_rc_api_call('/restapi/v1.0/account/~/business-hours', task_id=task_id, method='GET', token=token, raise_error=False)
    business_address = safe_rc_api_call('/restapi/v1.0/account/~/business-address', task_id=task_id, method='GET', token=token, raise_error=False)
    company_answering_rules = fetch_all_pages('/restapi/v1.0/account/~/answering-rule?view=Detailed', token, task_id)

    update_progress(task_id, 8, 100, "Fetching Paging & Park Locations...")
    paging_groups = fetch_all_pages('/restapi/v1.0/account/~/paging-only-groups', token, task_id)
    park_locations = fetch_all_pages('/restapi/v1.0/account/~/park-locations', token, task_id)

    update_progress(task_id, 10, 100, "Fetching Extensions...")
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token, task_id)

    config_data = {
        "account_info": safe_rc_api_call('/restapi/v1.0/account/~', task_id=task_id, method='GET', token=token, raise_error=False),
        "sites": sites,
        "cost_centers": cost_centers,
        "custom_roles": custom_roles,
        "templates": templates,
        "call_recording": call_recording,
        "company_business_hours": company_business_hours,
        "business_address": business_address,
        "company_answering_rules": company_answering_rules,
        "paging_groups": paging_groups,
        "park_locations": park_locations,
        "phone_numbers": phone_numbers,
        "devices": devices,
        "emergency_locations": emergency_locations,
        "extensions_raw": extensions,
        "detailed_extensions": {},
        "custom_audio_map": []
    }

    total_exts = max(len(extensions), 1)
    pull_audio = zip_file is not None and download_audio

    # ONLY these types actually support Answering Rules and Forwarding Numbers in the RC API
    VALID_CALL_HANDLING_TYPES = [
        'User', 'Department', 'VirtualUser', 'DigitalUser',
        'FlexibleUser', 'Voicemail', 'MessageOnly', 'Announcement', 'AnnouncementOnly'
    ]

    def _record_audio(entry, audio_uri):
        # Export path: download the bytes into the archive and tag the entry with
        # its filename (needed on import). Audit path: just record that the
        # greeting is custom, without pulling the audio.
        if pull_audio:
            try:
                audio_bytes, mime = download_audio_content(audio_uri, token, task_id)
                if audio_bytes:
                    file_ext = 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'wav'
                    filename = f"audio/{entry['ext_id']}_{entry['rule_id']}_{entry['greeting_type']}.{file_ext}"
                    zip_file.writestr(filename, audio_bytes)
                    entry["filename"] = filename
                    config_data["custom_audio_map"].append(entry)
            except Exception:
                pass
        else:
            config_data["custom_audio_map"].append(entry)

    for i, ext in enumerate(extensions):
        ext_id = str(ext['id'])
        ext_type = ext.get('type')
        ext_name = ext.get('name', 'Unknown')

        update_progress(task_id, 10 + int((i/total_exts)*80), 100, f"Extracting {ext_type}: {ext_name}...")

        ext_details = {"base_info": ext}

        # Only query deep routing for compatible extension types to prevent 403 Forbidden errors
        if ext_type in VALID_CALL_HANDLING_TYPES:
            ext_details["business_hours"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', task_id=task_id, method='GET', token=token, raise_error=False)
            ext_details["forwarding_numbers"] = fetch_all_pages(f'/restapi/v1.0/account/~/extension/{ext_id}/forwarding-number', token, task_id)
            ext_details["caller_id"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/caller-id', task_id=task_id, method='GET', token=token, raise_error=False)
            ext_details["notification_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings', task_id=task_id, method='GET', token=token, raise_error=False)
            ext_details["caller_blocking"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/caller-blocking', task_id=task_id, method='GET', token=token, raise_error=False)
            ext_details["caller_blocking_numbers"] = fetch_all_pages(f'/restapi/v1.0/account/~/extension/{ext_id}/caller-blocking/phone-numbers', token, task_id)

            # User-only surfaces: BLF/monitored lines and the assigned role.
            if ext_type == 'User':
                ext_details["presence_line"] = fetch_all_pages(f'/restapi/v1.0/account/~/extension/{ext_id}/presence/line', token, task_id)
                ext_details["assigned_role"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/assigned-role', task_id=task_id, method='GET', token=token, raise_error=False)

            rules_resp = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed', task_id=task_id, method='GET', token=token, raise_error=False)
            answering_rules = rules_resp.get('records', []) if rules_resp else []
            ext_details["answering_rules"] = answering_rules

            for rule in answering_rules:
                rule_id = rule.get('id')
                for greeting in rule.get('greetings', []):
                    if greeting.get('type') != 'Default' and greeting.get('custom'):
                        audio_id = greeting['custom'].get('id')
                        audio_uri = greeting['custom'].get('uri')
                        if audio_id and audio_uri:
                            _record_audio({
                                "ext_id": ext_id,
                                "ext_type": ext_type,
                                "ext_name": ext_name,
                                "rule_id": rule_id,
                                "greeting_type": greeting['type'],
                                "audio_id": audio_id,
                            }, audio_uri)

        # Special Configurations based on Extension Type
        if ext_type == 'Department':
            ext_details["queue_members"] = fetch_all_pages(f'/restapi/v1.0/account/~/call-queues/{ext_id}/members', token, task_id)
            ext_details["queue_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/call-queues/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)
        elif ext_type == 'IvrMenu':
            ivr_info = safe_rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)
            ext_details["ivr_settings"] = ivr_info
            if ivr_info and ivr_info.get('prompt', {}).get('mode') == 'Audio':
                audio_uri = ivr_info['prompt'].get('audio', {}).get('uri')
                audio_id = ivr_info['prompt'].get('audio', {}).get('id')
                if audio_uri:
                    _record_audio({
                        "ext_id": ext_id,
                        "ext_type": ext_type,
                        "ext_name": ext_name,
                        "rule_id": "ivr_prompt",
                        "greeting_type": "IvrPrompt",
                        "audio_id": audio_id,
                    }, audio_uri)
        elif ext_type in ['Announcement', 'AnnouncementOnly']:
            ext_details["announcement_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)
        elif ext_type in ['MessageOnly', 'Voicemail']:
            ext_details["message_only_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)

        config_data["detailed_extensions"][ext_id] = ext_details
        time.sleep(0.05)

    return config_data


# --- EXPORT LOGIC ---
def run_account_export(task_id, token=None):
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        config_data = _collect_config_data(task_id, token=token, zip_file=zip_file, download_audio=True)
        _set_account_name(task_id, config_data)
        extensions = config_data["extensions_raw"]
        cost_centers = config_data["cost_centers"]
        phone_numbers = config_data["phone_numbers"]
        devices = config_data["devices"]

        update_progress(task_id, 95, 100, "Compiling Configuration Files...")
        zip_file.writestr("config.json", json.dumps(config_data, indent=4))
        
        # Flattens complex dicts/arrays into strings so Pandas doesn't crash on Excel conversion
        def flatten_dict_for_excel(record_list):
            flat_list = []
            for r in record_list:
                flat_r = {}
                for k, v in r.items():
                    if isinstance(v, (dict, list)):
                        flat_r[k] = json.dumps(v)
                    else:
                        flat_r[k] = v
                flat_list.append(flat_r)
            return flat_list
            
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
            pd.DataFrame(flatten_dict_for_excel(extensions)).to_excel(writer, sheet_name="Extensions", index=False)
            if cost_centers:
                pd.DataFrame(flatten_dict_for_excel(cost_centers)).to_excel(writer, sheet_name="Cost Centers", index=False)
            else:
                pd.DataFrame([{"Notice": "No Cost Centers Found"}]).to_excel(writer, sheet_name="Cost Centers", index=False)
            pd.DataFrame(flatten_dict_for_excel(phone_numbers)).to_excel(writer, sheet_name="Phone Numbers", index=False)
            pd.DataFrame(flatten_dict_for_excel(devices)).to_excel(writer, sheet_name="Devices", index=False)
            if config_data["custom_audio_map"]:
                pd.DataFrame(flatten_dict_for_excel(config_data["custom_audio_map"])).to_excel(writer, sheet_name="Audio Mappings", index=False)
        zip_file.writestr("Account_Audit.xlsx", excel_buffer.getvalue())

    # Non-terminal: the background runner marks 'completed' only after the ZIP is
    # stored, so a status poll can't report done before the file is downloadable.
    update_progress(task_id, 98, 100, "Finalising export…")
    zip_buffer.seek(0)
    return zip_buffer


# --- READER-FRIENDLY AUDIT ---
def _display_name(base):
    """Best human-readable label for an extension: its name, else the contact's
    full name, else the extension number / id."""
    base = base or {}
    name = base.get('name')
    if name:
        return name
    c = base.get('contact') or {}
    full = ' '.join(x for x in [c.get('firstName'), c.get('lastName')] if x)
    return full or str(base.get('extensionNumber') or base.get('id') or '')


def _build_audit_workbook(config_data):
    """Render the collected tenant data as a formatted, multi-sheet Excel
    workbook meant for a person to read: one clean sheet per category, friendly
    column headers, frozen header row, sized columns, and no raw JSON blobs."""
    detailed = config_data.get('detailed_extensions', {})
    exts_raw = config_data.get('extensions_raw', [])

    ext_by_id = {str(e.get('id')): e for e in exts_raw}

    def assigned_to(ref):
        ref = ref or {}
        eid = ref.get('id')
        if not eid:
            return ''
        e = ext_by_id.get(str(eid))
        if not e:
            return ref.get('name') or str(eid)
        num = e.get('extensionNumber')
        return f"{_display_name(e)}" + (f" (Ext {num})" if num else '')

    def ext_sort_key(row):
        val = row.get('Extension #', '')
        try:
            return (0, int(val))
        except (TypeError, ValueError):
            return (1, str(val))

    # --- Summary ---
    ai = config_data.get('account_info') or {}
    type_counts = {}
    for e in exts_raw:
        t = e.get('type', 'Unknown')
        type_counts[t] = type_counts.get(t, 0) + 1
    summary_rows = [
        {"Metric": "Account Name", "Value": ai.get('name', '')},
        {"Metric": "Account ID", "Value": ai.get('id', '')},
        {"Metric": "Main Number", "Value": ai.get('mainNumber', '')},
        {"Metric": "Total Extensions", "Value": len(exts_raw)},
    ]
    for t in sorted(type_counts):
        summary_rows.append({"Metric": f"    • {t}", "Value": type_counts[t]})
    summary_rows += [
        {"Metric": "Sites", "Value": len(config_data.get('sites', []))},
        {"Metric": "Cost Centers", "Value": len(config_data.get('cost_centers', []))},
        {"Metric": "Custom Roles", "Value": len(config_data.get('custom_roles', []))},
        {"Metric": "Phone Numbers", "Value": len(config_data.get('phone_numbers', []))},
        {"Metric": "Devices", "Value": len(config_data.get('devices', []))},
        {"Metric": "Emergency Locations", "Value": len(config_data.get('emergency_locations', []))},
        {"Metric": "Templates", "Value": len(config_data.get('templates', []))},
        {"Metric": "Custom Greetings", "Value": len(config_data.get('custom_audio_map', []))},
    ]

    # --- Users & Extensions ---
    ext_rows = []
    for _eid, d in detailed.items():
        b = d.get('base_info', {})
        c = b.get('contact') or {}
        roles = ((d.get('assigned_role') or {}).get('records')) or []
        role_label = ', '.join(r.get('displayName') or str(r.get('id', '')) for r in roles)
        ext_rows.append({
            "Extension #": b.get('extensionNumber', ''),
            "Name": _display_name(b),
            "Type": b.get('type', ''),
            "Status": b.get('status', ''),
            "Email": c.get('email', ''),
            "Department": c.get('department', ''),
            "Site": (b.get('site') or {}).get('name', ''),
            "Cost Center": (b.get('costCenter') or {}).get('name', ''),
            "Role": role_label,
        })
    ext_rows.sort(key=ext_sort_key)

    # --- Call Queues ---
    queue_rows = []
    for _eid, d in detailed.items():
        b = d.get('base_info', {})
        if b.get('type') != 'Department':
            continue
        members = d.get('queue_members', []) or []
        member_names = ', '.join(str(m.get('name') or m.get('extensionNumber') or '') for m in members)
        queue_rows.append({
            "Queue Name": _display_name(b),
            "Extension #": b.get('extensionNumber', ''),
            "Site": (b.get('site') or {}).get('name', ''),
            "Member Count": len(members),
            "Members": member_names,
        })
    queue_rows.sort(key=ext_sort_key)

    # --- IVR Menus ---
    ivr_rows = []
    for _eid, d in detailed.items():
        b = d.get('base_info', {})
        if b.get('type') != 'IvrMenu':
            continue
        s = d.get('ivr_settings') or {}
        prompt = s.get('prompt') or {}
        ivr_rows.append({
            "IVR Name": _display_name(b),
            "Extension #": b.get('extensionNumber', ''),
            "Prompt Mode": prompt.get('mode', ''),
            "Actions Defined": len(s.get('actions', []) or []),
        })
    ivr_rows.sort(key=ext_sort_key)

    # --- Sites ---
    site_rows = [{
        "Site Name": s.get('name', ''),
        "Extension #": s.get('extensionNumber', ''),
        "Site ID": s.get('id', ''),
    } for s in config_data.get('sites', [])]

    # --- Cost Centers ---
    cc_rows = [{
        "Name": c.get('name', ''),
        "Billing Code": c.get('billingCode', ''),
    } for c in config_data.get('cost_centers', [])]

    # --- Custom Roles ---
    role_rows = [{
        "Role Name": r.get('displayName', r.get('id', '')),
        "Based On": r.get('basedOn', ''),
        "Description": r.get('description', ''),
    } for r in config_data.get('custom_roles', [])]

    # --- Phone Numbers ---
    pn_rows = [{
        "Phone Number": n.get('phoneNumber', ''),
        "Type": n.get('type', ''),
        "Usage": n.get('usageType', ''),
        "Status": n.get('status', ''),
        "Label": n.get('label', ''),
        "Assigned To": assigned_to(n.get('extension')),
    } for n in config_data.get('phone_numbers', [])]

    # --- Devices ---
    dev_rows = [{
        "Device Name": dv.get('name', ''),
        "Type": dv.get('type', ''),
        "Model": (dv.get('model') or {}).get('name', ''),
        "Serial / MAC": dv.get('serial', ''),
        "Site": (dv.get('site') or {}).get('name', ''),
        "Status": dv.get('status', ''),
        "Assigned To": assigned_to(dv.get('extension')) or 'Unassigned',
    } for dv in config_data.get('devices', [])]

    # --- Emergency Locations (ERLs) ---
    def _compact_address(addr):
        if not isinstance(addr, dict):
            return ''
        parts = []
        for v in addr.values():
            if isinstance(v, dict):
                v = v.get('name') or v.get('id')
            if v not in (None, ''):
                parts.append(str(v))
        return ', '.join(parts)

    erl_rows = [{
        "Name": e.get('name', ''),
        "Visibility": e.get('visibility', ''),
        "Site": (e.get('site') or {}).get('name', ''),
        "Address": _compact_address(e.get('address')),
        "Address Status": e.get('addressStatus', ''),
        "Usage Status": e.get('usageStatus', ''),
    } for e in config_data.get('emergency_locations', [])]

    # --- Answering Rules (custom, per extension) ---
    ar_rows = []
    for _eid, d in detailed.items():
        b = d.get('base_info', {})
        for rule in d.get('answering_rules', []) or []:
            if rule.get('type') != 'Custom':
                continue
            ar_rows.append({
                "Extension": _display_name(b),
                "Ext #": b.get('extensionNumber', ''),
                "Rule Name": rule.get('name', ''),
                "Enabled": "Yes" if rule.get('enabled') else "No",
                "Call Handling": rule.get('callHandlingAction', ''),
            })

    # --- Custom Greetings ---
    greet_rows = [{
        "Extension": a.get('ext_name', ''),
        "Extension Type": a.get('ext_type', ''),
        "Greeting Type": a.get('greeting_type', ''),
    } for a in config_data.get('custom_audio_map', [])]

    # --- Templates ---
    tpl_rows = [{"Template Name": t.get('name', '')} for t in config_data.get('templates', [])]

    sheets = [
        ("Summary", summary_rows, ["Metric", "Value"]),
        ("Users & Extensions", ext_rows, ["Extension #", "Name", "Type", "Status", "Email", "Department", "Site", "Cost Center", "Role"]),
        ("Call Queues", queue_rows, ["Queue Name", "Extension #", "Site", "Member Count", "Members"]),
        ("IVR Menus", ivr_rows, ["IVR Name", "Extension #", "Prompt Mode", "Actions Defined"]),
        ("Answering Rules", ar_rows, ["Extension", "Ext #", "Rule Name", "Enabled", "Call Handling"]),
        ("Phone Numbers", pn_rows, ["Phone Number", "Type", "Usage", "Status", "Label", "Assigned To"]),
        ("Devices", dev_rows, ["Device Name", "Type", "Model", "Serial / MAC", "Site", "Status", "Assigned To"]),
        ("Emergency Locations", erl_rows, ["Name", "Visibility", "Site", "Address", "Address Status", "Usage Status"]),
        ("Sites", site_rows, ["Site Name", "Extension #", "Site ID"]),
        ("Cost Centers", cc_rows, ["Name", "Billing Code"]),
        ("Custom Roles", role_rows, ["Role Name", "Based On", "Description"]),
        ("Custom Greetings", greet_rows, ["Extension", "Extension Type", "Greeting Type"]),
        ("Templates", tpl_rows, ["Template Name"]),
    ]

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        book = writer.book
        header_fmt = book.add_format({
            'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white',
            'border': 1, 'align': 'left', 'valign': 'vcenter',
        })

        for sheet_name, rows, columns in sheets:
            if rows:
                df = pd.DataFrame(rows, columns=columns)
            else:
                # Keep a non-empty sheet so reviewers see the category exists.
                df = pd.DataFrame([{columns[0]: "None found"}], columns=columns)
            df = df.fillna('')
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            ws = writer.sheets[sheet_name]
            for col_idx, col in enumerate(df.columns):
                ws.write(0, col_idx, str(col), header_fmt)
                width = len(str(col))
                for value in df[col].astype(str).tolist():
                    width = max(width, min(len(value), 60))
                ws.set_column(col_idx, col_idx, width + 2)
            ws.freeze_panes(1, 0)

    return excel_buffer


def run_account_audit(task_id, token=None):
    """Full-scale, reader-friendly account audit: the same deep read-only tenant
    scan the migration export performs, rendered as a formatted multi-sheet Excel
    workbook for reviewing an account at a glance. Read-only — nothing on the
    tenant is changed and no audio is downloaded."""
    config_data = _collect_config_data(task_id, token=token, zip_file=None, download_audio=False)
    _set_account_name(task_id, config_data)
    update_progress(task_id, 92, 100, "Building reader-friendly audit workbook...")
    excel_buffer = _build_audit_workbook(config_data)
    # Non-terminal: the background runner marks 'completed' after the workbook is
    # stored (see run_audit_background), so the poll never reports done early.
    update_progress(task_id, 98, 100, "Finalising audit…")
    excel_buffer.seek(0)
    return excel_buffer


# --- BACKGROUND RUNNERS + DURABLE STORAGE -----------------------------------
# Export and Audit now run in a daemon thread (Import already did) so a large
# account can't overrun the Cloud Run request timeout, and the SM bridge is kept
# alive by the self-healing _MigCaller for the whole run. Each finished artifact
# is persisted (GCS + Firestore index) for later retrieval.

def _set_account_name(task_id, config_data):
    """Stash the account name on the progress entry for status + storage labels."""
    info = (config_data or {}).get("account_info") or {}
    name = info.get("name") or ""
    entry = migration_progress_store.get(task_id)
    if entry is not None and name:
        entry["account_name"] = name


def _run_with_auth(app, task_id, auth_data, fn):
    """Run fn() inside an app context with a self-healing auth holder installed,
    so RC calls resolve config and re-mint the bridge token without a session."""
    token_ctx = None
    try:
        with app.app_context():
            token_ctx = _mig_ctx.set(_MigCaller(auth_data))
            return fn()
    finally:
        if token_ctx is not None:
            _mig_ctx.reset(token_ctx)


def run_export_background(app, task_id, auth_data, user_email=None):
    from . import storage
    try:
        storage.record_status(task_id, "running", kind="export", user_email=user_email)

        def _do():
            buf = run_account_export(task_id, auth_data.get("access_token"))
            data = buf.getvalue()
            entry = migration_progress_store.get(task_id, {})
            fname = f"RC_Migration_Export_{int(time.time())}.zip"
            entry["file_data"] = data
            entry["download_name"] = fname
            entry["content_type"] = "application/zip"
            entry["kind"] = "export"
            migration_progress_store[task_id] = entry
            storage.save_result(task_id, "export", data, fname, "application/zip",
                                user_email, account_name=entry.get("account_name"))
            # Now that the file is downloadable, signal completion.
            update_progress(task_id, 100, 100, "Export complete! Downloading…",
                            status="completed")
        _run_with_auth(app, task_id, auth_data, _do)
    except Exception as e:
        logger.exception("[account_migration] export failed")
        update_progress(task_id, 0, 100, f"Export Error: {e}", status="error")
        try:
            storage.record_status(task_id, "error", kind="export",
                                  user_email=user_email, error=str(e))
        except Exception:
            pass


def run_audit_background(app, task_id, auth_data, user_email=None):
    from . import storage
    try:
        storage.record_status(task_id, "running", kind="audit", user_email=user_email)

        def _do():
            buf = run_account_audit(task_id, auth_data.get("access_token"))
            data = buf.getvalue()
            entry = migration_progress_store.get(task_id, {})
            fname = f"RC_Account_Audit_{int(time.time())}.xlsx"
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            entry["file_data"] = data
            entry["download_name"] = fname
            entry["content_type"] = ctype
            entry["kind"] = "audit"
            migration_progress_store[task_id] = entry
            storage.save_result(task_id, "audit", data, fname, ctype,
                                user_email, account_name=entry.get("account_name"))
            update_progress(task_id, 100, 100, "Audit complete! Downloading…",
                            status="completed")
        _run_with_auth(app, task_id, auth_data, _do)
    except Exception as e:
        logger.exception("[account_migration] audit failed")
        update_progress(task_id, 0, 100, f"Audit Error: {e}", status="error")
        try:
            storage.record_status(task_id, "error", kind="audit",
                                  user_email=user_email, error=str(e))
        except Exception:
            pass


def _build_results_workbook(results):
    """Build an xlsx from a run's per-item results (Category/Item/Status/Detail)."""
    buf = io.BytesIO()
    rows = results or [{"Category": "—", "Item": "No results recorded",
                        "Status": "—", "Detail": ""}]
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Results", index=False)
    buf.seek(0)
    return buf.getvalue()


def _persist_import_log(task_id, account_name, user_email):
    """Save the import's results log to durable storage for later audit."""
    from . import storage
    try:
        entry = migration_progress_store.get(task_id) or {}
        data = _build_results_workbook(entry.get("results"))
        fname = f"RC_Import_Results_{int(time.time())}.xlsx"
        ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        entry["file_data"] = data
        entry["download_name"] = fname
        entry["content_type"] = ctype
        entry["kind"] = "import"
        migration_progress_store[task_id] = entry
        storage.save_result(task_id, "import", data, fname, ctype,
                            user_email, account_name=account_name)
    except Exception:
        logger.exception("[account_migration] import log persist failed")


def run_import_background(app, task_id, zip_bytes, auth_data, user_email=None):
    """Run the account import under an app context + self-healing auth holder,
    then persist its results log for later audit retrieval. run_account_import
    owns its own status/error handling and cancel-flag clearing."""
    def _do():
        run_account_import(task_id, zip_bytes, auth_data.get("access_token"))
    _run_with_auth(app, task_id, auth_data, _do)
    entry = migration_progress_store.get(task_id) or {}
    _persist_import_log(task_id, entry.get("account_name"), user_email)


# --- IMPORT HELPERS ---

# Server-assigned / account-scoped fields that must never be POSTed/PUT back onto
# a different tenant. Stripped from every round-tripped object before it is sent.
_READONLY_KEYS = ('id', 'uri', 'extensionNumber', 'status', 'creationTime',
                  'lastModifiedTime', 'serviceFeatures', 'permissions', 'account')

# Default answering-rule identifiers are constant strings across every account,
# so they pass through a migration unchanged; only Custom rule ids are account
# -specific and need remapping.
_DEFAULT_RULE_IDS = ('business-hours-rule', 'after-hours-rule')


def _clean(obj, drop=_READONLY_KEYS):
    """Return a shallow copy of a dict with server-assigned keys removed, so the
    captured object can be re-created on the destination tenant."""
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k not in drop and v is not None}


def _mapped_ref(ref, mapping):
    """Remap a {'id': old} reference through mapping. Returns {'id': new} if the
    id is known on the destination, else None so the caller can drop it."""
    if not isinstance(ref, dict):
        return None
    old = str(ref.get('id'))
    if old in mapping:
        return {"id": mapping[old]}
    return None


def _norm_mac(value):
    """Normalise a MAC/serial to lowercase hex only (strip : - . spaces),
    matching the Device Swap tool's expectation for /device/bulk-update."""
    return ''.join(c for c in str(value or '') if c in '0123456789abcdefABCDEF').lower()


# --- IMPORT LOGIC ---
def run_account_import(task_id, zip_bytes, token=None):
    try:
        update_progress(task_id, 0, 100, "Extracting and Parsing ZIP...", status='running')
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_ref:
            if "config.json" not in zip_ref.namelist():
                update_progress(task_id, 0, 100, "Invalid ZIP: Missing config.json", status='error')
                return

            config = json.loads(zip_ref.read("config.json"))
            _set_account_name(task_id, config)  # source account, for the log label
            audio_map = config.get("custom_audio_map", [])
            detailed_exts = config.get("detailed_extensions", {})

            old_to_new_sites = {}
            old_to_new_cost_centers = {}
            old_to_new_roles = {}
            old_to_new_exts = {}
            # (old_ext_id, old_rule_id) -> new_rule_id, so migrated custom greetings
            # attach to the rule that actually exists on the destination.
            old_to_new_rules = {}

            # ============================================================
            # Pass 0: Account-level settings
            # ============================================================
            update_progress(task_id, 2, 100, "Applying account-level settings...")
            if config.get("call_recording"):
                try:
                    safe_rc_api_call('/restapi/v1.0/account/~/call-recording', task_id=task_id, method='PUT', json_payload=_clean(config["call_recording"]), token=token, raise_error=False)
                    add_result(task_id, 'Account', 'Call recording settings', 'Applied')
                except Exception as e:
                    add_result(task_id, 'Account', 'Call recording settings', 'Failed', str(e))

            if config.get("company_business_hours", {}).get("schedule"):
                try:
                    safe_rc_api_call('/restapi/v1.0/account/~/business-hours', task_id=task_id, method='PUT', json_payload={"schedule": config["company_business_hours"]["schedule"]}, token=token, raise_error=False)
                    add_result(task_id, 'Account', 'Company business hours', 'Applied')
                except Exception as e:
                    add_result(task_id, 'Account', 'Company business hours', 'Failed', str(e))

            if config.get("business_address", {}).get("business"):
                try:
                    safe_rc_api_call('/restapi/v1.0/account/~/business-address', task_id=task_id, method='PUT', json_payload=_clean(config["business_address"]), token=token, raise_error=False)
                    add_result(task_id, 'Account', 'Business address', 'Applied')
                except Exception as e:
                    add_result(task_id, 'Account', 'Business address', 'Failed', str(e))

            # ============================================================
            # Pass 1: Cost centers
            # ============================================================
            for cc in config.get("cost_centers", []):
                if _stopped_and_marked(task_id):
                    return
                try:
                    payload = {"name": cc['name'], "billingCode": cc.get('billingCode')}
                    new_cc = safe_rc_api_call('/restapi/v1.0/account/~/cost-center', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    old_to_new_cost_centers[str(cc['id'])] = str(new_cc['id'])
                    add_result(task_id, 'Cost Center', cc.get('name', ''), 'Created')
                except Exception as e:
                    add_result(task_id, 'Cost Center', cc.get('name', ''), 'Failed', str(e))

            # ============================================================
            # Pass 2: Custom roles
            # ============================================================
            update_progress(task_id, 6, 100, "Recreating custom roles...")
            for role in config.get("custom_roles", []):
                if _stopped_and_marked(task_id):
                    return
                try:
                    payload = _clean(role, drop=('id', 'uri', 'lastUpdated', 'default', 'assignable'))
                    new_role = safe_rc_api_call('/restapi/v1.0/account/~/custom-roles', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    old_to_new_roles[str(role['id'])] = str(new_role['id'])
                    add_result(task_id, 'Custom Role', role.get('displayName', role.get('id', '')), 'Created')
                except Exception as e:
                    add_result(task_id, 'Custom Role', role.get('displayName', role.get('id', '')), 'Failed', str(e))

            # ============================================================
            # Pass 3: Sites
            # ============================================================
            update_progress(task_id, 10, 100, "Recreating sites...")
            for site in config.get("sites", []):
                if _stopped_and_marked(task_id):
                    return
                if site['id'] == 'main-site':
                    old_to_new_sites[site['id']] = 'main-site'
                    continue
                try:
                    payload = {"name": site['name'], "extensionNumber": site.get('extensionNumber')}
                    if site.get('businessHours', {}).get('schedule'):
                        payload['businessHours'] = {"schedule": site['businessHours']['schedule']}
                    if site.get('regionalSettings'):
                        payload['regionalSettings'] = site['regionalSettings']
                    new_site = safe_rc_api_call('/restapi/v1.0/account/~/sites', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    old_to_new_sites[site['id']] = str(new_site['id'])
                    add_result(task_id, 'Site', site.get('name', ''), 'Created')
                except Exception as e:
                    add_result(task_id, 'Site', site.get('name', ''), 'Failed', str(e))

            # ============================================================
            # Pass 3b: Emergency Response Locations (ERLs)
            # Recreate the address definitions themselves — depends on sites
            # (Pass 3) for the site remap. The captured object is RC's own stored
            # shape, so its address/addressFormatId round-trip as-is. Per-number /
            # per-device E911 assignment and carrier validation remain manual
            # (see the manual worklist).
            # ============================================================
            update_progress(task_id, 14, 100, "Recreating emergency response locations...")
            for erl in config.get("emergency_locations", []):
                if _stopped_and_marked(task_id):
                    return
                erl_name = erl.get('name', '') or 'Emergency location'
                try:
                    payload = {"name": erl.get('name')}
                    if erl.get('visibility'):
                        payload['visibility'] = erl['visibility']
                    site_ref = _mapped_ref(erl.get('site'), old_to_new_sites)
                    if site_ref:
                        payload['site'] = site_ref
                    if erl.get('addressFormatId'):
                        payload['addressFormatId'] = erl['addressFormatId']
                    if erl.get('address'):
                        payload['address'] = erl['address']
                    safe_rc_api_call('/restapi/v1.0/account/~/emergency-locations', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    add_result(task_id, 'Emergency Location', erl_name, 'Created')
                except Exception as e:
                    add_result(task_id, 'Emergency Location', erl_name, 'Failed', str(e))

            # ============================================================
            # Pass 4: Group / structure extensions
            # (Park, Paging, Queue, IVR, Announcement, Message-Only)
            # ============================================================
            update_progress(task_id, 16, 100, "Recreating extension structures...")
            for old_id, details in detailed_exts.items():
                if _stopped_and_marked(task_id):
                    return
                ext_type = details['base_info'].get('type')
                if ext_type in ('User', 'Limited'):
                    continue  # handled in Pass 5

                payload = {"extensionNumber": details['base_info'].get('extensionNumber')}
                if 'name' in details['base_info']:
                    payload['name'] = details['base_info']['name']
                site_ref = _mapped_ref(details['base_info'].get('site'), old_to_new_sites)
                if site_ref:
                    payload['site'] = site_ref
                cc_ref = _mapped_ref(details['base_info'].get('costCenter'), old_to_new_cost_centers)
                if cc_ref:
                    payload['costCenter'] = cc_ref

                try:
                    if ext_type == 'ParkLocation':
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/park-locations', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    elif ext_type == 'PagingOnly':
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/paging-only-groups', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    elif ext_type == 'Department':
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/call-queues', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    elif ext_type == 'IvrMenu':
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/ivr-menus', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    elif ext_type in ['Announcement', 'AnnouncementOnly', 'MessageOnly', 'Voicemail']:
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/extension', task_id=task_id, method='POST', json_payload={"extensionNumber": payload.get("extensionNumber"), "type": ext_type, "contact": {"firstName": payload.get("name", ext_type)}}, token=token, raise_error=True)
                    else:
                        continue
                    old_to_new_exts[str(old_id)] = str(new_ext['id'])
                    add_result(task_id, ext_type or 'Extension', payload.get('name', payload.get('extensionNumber', old_id)), 'Created')
                except Exception as e:
                    add_result(task_id, ext_type or 'Extension', payload.get('name', payload.get('extensionNumber', old_id)), 'Failed', str(e))

            # ============================================================
            # Pass 5: User & Limited extensions
            # Prefer mapping config onto UNASSIGNED extensions pre-provisioned on
            # the winning account (the documented workflow); fall back to POST
            # create only if a spare licensed slot is available.
            # ============================================================
            update_progress(task_id, 30, 100, "Migrating users onto the winning account...")
            target_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token, task_id)
            used_numbers = {str(e.get('extensionNumber')) for e in target_exts if e.get('extensionNumber')}
            pools = {
                'User': [e for e in target_exts if e.get('type') == 'User' and e.get('status') in ('Unassigned', 'NotActivated')],
                'Limited': [e for e in target_exts if e.get('type') == 'Limited' and e.get('status') in ('Unassigned', 'NotActivated')],
            }

            for old_id, details in detailed_exts.items():
                if _stopped_and_marked(task_id):
                    return
                base = details['base_info']
                ext_type = base.get('type')
                if ext_type not in ('User', 'Limited'):
                    continue

                label = base.get('name') or base.get('extensionNumber') or old_id
                contact = _clean(base.get('contact', {}), drop=('id', 'uri'))
                src_number = str(base.get('extensionNumber')) if base.get('extensionNumber') else None

                put_payload = {}
                if contact:
                    put_payload['contact'] = contact
                if src_number and src_number not in used_numbers:
                    put_payload['extensionNumber'] = src_number
                site_ref = _mapped_ref(base.get('site'), old_to_new_sites)
                if site_ref:
                    put_payload['site'] = site_ref
                cc_ref = _mapped_ref(base.get('costCenter'), old_to_new_cost_centers)
                if cc_ref:
                    put_payload['costCenter'] = cc_ref
                if base.get('regionalSettings'):
                    put_payload['regionalSettings'] = base['regionalSettings']

                try:
                    pool = pools.get(ext_type, [])
                    if pool:
                        target = pool.pop(0)
                        new_id = str(target['id'])
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}', task_id=task_id, method='PUT', json_payload=put_payload, token=token, raise_error=True)
                        old_to_new_exts[str(old_id)] = new_id
                        if src_number and src_number not in used_numbers:
                            used_numbers.add(src_number)
                        add_result(task_id, ext_type, label, 'Assigned', 'Mapped onto a pre-provisioned extension')
                    else:
                        create_payload = {"type": ext_type, "contact": contact or {"firstName": str(label)}}
                        if src_number and src_number not in used_numbers:
                            create_payload['extensionNumber'] = src_number
                        new_ext = safe_rc_api_call('/restapi/v1.0/account/~/extension', task_id=task_id, method='POST', json_payload=create_payload, token=token, raise_error=True)
                        new_id = str(new_ext['id'])
                        old_to_new_exts[str(old_id)] = new_id
                        if src_number and src_number not in used_numbers:
                            used_numbers.add(src_number)
                        add_result(task_id, ext_type, label, 'Created', 'No spare extension in pool — created new (needs a free license)')
                except Exception as e:
                    add_result(task_id, ext_type, label, 'Failed', f"No pre-provisioned extension available and create failed: {e}")

            # ============================================================
            # Pass 6: Per-extension configuration (needs the ext to exist)
            # ============================================================
            update_progress(task_id, 45, 100, "Applying per-extension configuration...")
            cfg_items = list(detailed_exts.items())
            total_cfg = max(len(cfg_items), 1)
            for i, (old_id, details) in enumerate(cfg_items):
                if _stopped_and_marked(task_id):
                    return
                new_id = old_to_new_exts.get(str(old_id))
                if not new_id:
                    continue
                base = details['base_info']
                label = base.get('name') or base.get('extensionNumber') or old_id
                update_progress(task_id, 45 + int((i / total_cfg) * 20), 100, f"Configuring {label}...")

                # Business hours
                bh = details.get('business_hours') or {}
                if bh.get('schedule'):
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/business-hours', task_id=task_id, method='PUT', json_payload={"schedule": bh['schedule']}, token=token, raise_error=False)
                    except Exception:
                        pass

                # Custom answering rules (default rules already exist on the ext)
                for rule in details.get('answering_rules', []):
                    if rule.get('type') != 'Custom':
                        continue
                    try:
                        # Drop objects that reference source-account devices/queues
                        # (they would 400 the whole rule). External unconditional
                        # forwarding and remapped transfer/voicemail are kept below.
                        rp = _clean(rule, drop=('id', 'uri', 'greetings', 'sharedLines', 'forwarding', 'queue'))
                        tref = _mapped_ref(rule.get('transfer', {}).get('extension'), old_to_new_exts)
                        if tref and 'transfer' in rp:
                            rp['transfer'] = {**rp['transfer'], 'extension': tref}
                        vref = _mapped_ref(rule.get('voicemail', {}).get('recipient'), old_to_new_exts)
                        if vref and 'voicemail' in rp:
                            rp['voicemail'] = {**rp['voicemail'], 'recipient': vref}
                        new_rule = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/answering-rule', task_id=task_id, method='POST', json_payload=rp, token=token, raise_error=True)
                        old_to_new_rules[(str(old_id), str(rule.get('id')))] = str(new_rule['id'])
                        add_result(task_id, 'Answering Rule', f"{label}: {rule.get('name', '')}", 'Created')
                    except Exception as e:
                        add_result(task_id, 'Answering Rule', f"{label}: {rule.get('name', '')}", 'Failed', str(e))

                # Forwarding numbers
                for fwd in details.get('forwarding_numbers', []):
                    try:
                        fp = _clean(fwd, drop=('id', 'uri'))
                        if fp.get('phoneNumber'):
                            safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/forwarding-number', task_id=task_id, method='POST', json_payload=fp, token=token, raise_error=False)
                    except Exception:
                        pass

                # Notification settings
                ns = details.get('notification_settings')
                if ns:
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/notification-settings', task_id=task_id, method='PUT', json_payload=_clean(ns), token=token, raise_error=False)
                    except Exception:
                        pass

                # Caller blocking (settings + explicit numbers)
                cb = details.get('caller_blocking')
                if cb:
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/caller-blocking', task_id=task_id, method='PUT', json_payload=_clean(cb), token=token, raise_error=False)
                    except Exception:
                        pass
                for num in details.get('caller_blocking_numbers', []):
                    try:
                        np = _clean(num, drop=('id', 'uri'))
                        if np.get('phoneNumber'):
                            safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/caller-blocking/phone-numbers', task_id=task_id, method='POST', json_payload=np, token=token, raise_error=False)
                    except Exception:
                        pass

                # Assigned role (remap custom-role ids; predefined ids pass through)
                ar = details.get('assigned_role')
                if ar and ar.get('records'):
                    recs = []
                    for r in ar['records']:
                        rid = str(r.get('id'))
                        recs.append({"id": old_to_new_roles.get(rid, rid)})
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/assigned-role', task_id=task_id, method='PUT', json_payload={"records": recs}, token=token, raise_error=False)
                    except Exception:
                        pass

                # Presence / BLF monitored lines (remap; drop unmapped)
                pl = details.get('presence_line') or []
                mon = []
                for line in pl:
                    ref = _mapped_ref(line.get('extension'), old_to_new_exts)
                    if ref:
                        mon.append({"extension": ref})
                if mon:
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_id}/presence/line', task_id=task_id, method='PUT', json_payload={"records": mon}, token=token, raise_error=False)
                    except Exception:
                        pass

            # ============================================================
            # Pass 7: Call queue settings, members & managers
            # ============================================================
            update_progress(task_id, 66, 100, "Applying queue settings and members...")
            for old_id, details in detailed_exts.items():
                if _stopped_and_marked(task_id):
                    return
                if details['base_info'].get('type') != 'Department':
                    continue
                new_id = old_to_new_exts.get(str(old_id))
                if not new_id:
                    continue
                label = details['base_info'].get('name', old_id)

                qs = details.get('queue_settings')
                if qs:
                    try:
                        qp = _clean(qs, drop=_READONLY_KEYS + ('site', 'serviceLevelSettings', 'name'))
                        if qp:
                            safe_rc_api_call(f'/restapi/v1.0/account/~/call-queues/{new_id}', task_id=task_id, method='PUT', json_payload=qp, token=token, raise_error=False)
                            add_result(task_id, 'Queue Settings', label, 'Applied')
                    except Exception as e:
                        add_result(task_id, 'Queue Settings', label, 'Failed', str(e))

                member_ids = []
                for m in details.get('queue_members', []):
                    mid = old_to_new_exts.get(str(m.get('id')))
                    if mid:
                        member_ids.append(mid)
                if member_ids:
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/call-queues/{new_id}/bulk-assign', task_id=task_id, method='POST', json_payload={"addedExtensionIds": member_ids}, token=token, raise_error=True)
                        add_result(task_id, 'Queue Members', label, 'Assigned', f"{len(member_ids)} member(s)")
                    except Exception as e:
                        add_result(task_id, 'Queue Members', label, 'Failed', str(e))

            # ============================================================
            # Pass 8: Templates
            # ============================================================
            update_progress(task_id, 72, 100, "Recreating user templates...")
            for tpl in config.get("templates", []):
                if _stopped_and_marked(task_id):
                    return
                try:
                    tp = _clean(tpl, drop=('id', 'uri', 'creationTime', 'lastModifiedTime'))
                    safe_rc_api_call('/restapi/v1.0/account/~/templates', task_id=task_id, method='POST', json_payload=tp, token=token, raise_error=True)
                    add_result(task_id, 'Template', tpl.get('name', ''), 'Created')
                except Exception as e:
                    add_result(task_id, 'Template', tpl.get('name', ''), 'Failed', str(e))

            # ============================================================
            # Pass 9: Audio uploads (custom greetings now bound to NEW rule ids)
            # ============================================================
            total_audio = max(len(audio_map), 1)
            for i, a_map in enumerate(audio_map):
                if _stopped_and_marked(task_id):
                    return
                update_progress(task_id, 78 + int((i / total_audio) * 14), 100, f"Uploading audio: {a_map['filename']}")
                new_ext_id = old_to_new_exts.get(str(a_map['ext_id']))
                if not new_ext_id:
                    add_result(task_id, 'Audio', a_map.get('filename', ''), 'Skipped', 'Target extension was not recreated')
                    continue
                try:
                    audio_bytes = zip_ref.read(a_map['filename'])
                    filename_clean = a_map['filename'].split('/')[-1]
                    if a_map['greeting_type'] == 'IvrPrompt':
                        files = {'attachment': (filename_clean, audio_bytes, 'audio/mpeg')}
                        prompt_res = safe_rc_api_call('/restapi/v1.0/account/~/ivr-prompts', task_id=task_id, method='POST', data={'name': filename_clean}, files=files, token=token, raise_error=True)
                        safe_rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{new_ext_id}', task_id=task_id, method='PUT', json_payload={"prompt": {"mode": "Audio", "audio": {"id": prompt_res['id']}}}, token=token, raise_error=True)
                    else:
                        # Resolve the rule id on the DESTINATION: constant default
                        # rule ids pass through; custom rules use the remap built
                        # in Pass 6. Without a match the source id would 404, so skip.
                        src_rule = str(a_map.get('rule_id'))
                        if src_rule in _DEFAULT_RULE_IDS:
                            dest_rule = src_rule
                        else:
                            dest_rule = old_to_new_rules.get((str(a_map['ext_id']), src_rule))
                        if not dest_rule:
                            add_result(task_id, 'Audio', a_map.get('filename', ''), 'Skipped', 'Matching answering rule was not recreated on target')
                            continue
                        metadata = {"type": a_map['greeting_type'], "answeringRule": {"id": dest_rule}}
                        files = {'json': ('request.json', json.dumps(metadata), 'application/json'), 'attachment': (filename_clean, audio_bytes, 'audio/mpeg')}
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_ext_id}/greeting', task_id=task_id, method='POST', files=files, token=token, raise_error=True)
                    add_result(task_id, 'Audio', a_map.get('filename', ''), 'Uploaded', f"{a_map.get('ext_name', '')} / {a_map.get('greeting_type', '')}")
                except Exception as e:
                    add_result(task_id, 'Audio', a_map.get('filename', ''), 'Failed', str(e))

            # ============================================================
            # Pass 10: Devices — push the source MAC/model onto each migrated
            # user's pre-provisioned device slot, via the same
            # /device/bulk-update path the Device Swap tool uses. Assumes the
            # MAC has already been freed on the losing account (a MAC can only
            # live on one tenant) and the winning user has an Existing Phone
            # slot from the license/device prerequisite.
            # ============================================================
            update_progress(task_id, 90, 100, "Pushing devices onto the winning account...")
            device_updates = []
            device_labels = {}
            for dev in config.get("devices", []):
                if _stopped_and_marked(task_id):
                    return
                serial = _norm_mac(dev.get('serial'))
                dev_type = dev.get('type')
                model_id = str((dev.get('model') or {}).get('id') or '')
                src_ext_id = str((dev.get('extension') or {}).get('id') or '')
                dev_name = dev.get('name', '') or 'Device'
                lbl = f"{dev_name} ({serial or 'no-MAC'})"

                # Only physical phones carry a MAC that can be moved between tenants.
                if not serial or dev_type not in ('HardPhone', 'OtherPhone'):
                    continue
                if not src_ext_id:
                    add_result(task_id, 'Device', lbl, 'Skipped', 'Device was unassigned on the source (no owning user)')
                    continue
                new_ext_id = old_to_new_exts.get(src_ext_id)
                if not new_ext_id:
                    add_result(task_id, 'Device', lbl, 'Skipped', 'Owning user was not migrated')
                    continue

                slot_resp = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_ext_id}/device', task_id=task_id, method='GET', token=token, raise_error=False)
                slots = (slot_resp or {}).get('records', [])
                # Prefer a physical slot, then softphone — matches Device Swap ordering.
                target = (next((d for d in slots if d.get('type') == 'HardPhone'), None)
                          or next((d for d in slots if d.get('type') == 'OtherPhone'), None)
                          or next((d for d in slots if d.get('type') == 'SoftPhone'), None))
                if not target:
                    add_result(task_id, 'Device', lbl, 'Manual', "Target user has no device slot — add an 'Existing Phone' device, then re-run import")
                    continue

                rec = {"deviceId": str(target['id']), "serial": serial}
                if model_id:
                    rec["model"] = {"id": model_id}
                if dev.get('name'):
                    rec["name"] = dev['name']
                device_updates.append(rec)
                device_labels[str(target['id'])] = lbl

            # Fire in chunks; /device/bulk-update returns a per-device result set.
            for chunk_start in range(0, len(device_updates), 50):
                if _stopped_and_marked(task_id):
                    return
                chunk = device_updates[chunk_start:chunk_start + 50]
                resp = safe_rc_api_call('/restapi/v1.0/account/~/device/bulk-update', task_id=task_id, method='POST', json_payload={"records": chunk}, token=token, raise_error=False)
                returned = (resp or {}).get('records', []) if isinstance(resp, dict) else []
                if returned:
                    for idx, api_rec in enumerate(returned):
                        did = str(api_rec.get('deviceId') or api_rec.get('id') or '')
                        rlbl = device_labels.get(did) or (device_labels.get(chunk[idx]['deviceId']) if idx < len(chunk) else did)
                        if api_rec.get('successful'):
                            add_result(task_id, 'Device', rlbl, 'Pushed', 'MAC/model assigned to the user device')
                        else:
                            err = api_rec.get('error') or {}
                            add_result(task_id, 'Device', rlbl, 'Failed', err.get('message') or err.get('description') or 'Rejected by RingCentral')
                else:
                    for rec in chunk:
                        add_result(task_id, 'Device', device_labels.get(rec['deviceId'], rec['deviceId']), 'Failed', 'No response from RingCentral for this device')

            # ============================================================
            # Pass 11: Manual worklist — things the platform won't let us copy.
            # Surfaced as result rows so the engineer has a concrete checklist.
            # ============================================================
            update_progress(task_id, 94, 100, "Compiling manual-steps worklist...")
            _emit_manual_worklist(task_id, config, detailed_exts, old_to_new_exts)

            update_progress(task_id, 100, 100, "Migration import completed. Review results and complete the manual worklist.", status='completed')

    except Exception as e:
        update_progress(task_id, 0, 100, f"Import Error: {str(e)}", status='error')
    finally:
        # Clear the stop flag whatever the outcome so a later import that reuses
        # this task_id doesn't inherit a stale cancel.
        task_control.clear(task_id)


def _emit_manual_worklist(task_id, config, detailed_exts, old_to_new_exts):
    """Record the config that cannot be migrated by API — carrier-owned numbers,
    licenses, E911 — as explicit 'Manual' result rows so nothing is silently
    dropped and the engineer gets a post-migration checklist. (Devices are now
    pushed automatically in Pass 10; only their per-device outcomes are logged.)"""
    # Phone numbers: carrier/billing-owned, cannot move between tenants.
    numbers = config.get("phone_numbers", [])
    dids = [n for n in numbers if n.get('usageType') in ('DirectNumber', 'MainCompanyNumber', 'CompanyNumber', 'AdditionalCompanyNumber')]
    if dids:
        add_result(task_id, 'Manual — Numbers', f"{len(dids)} phone number(s)", 'Manual',
                   'Carrier-owned — cannot copy. Assign temporary ALNs at cutover, then port the real numbers and re-map with the Phone Number Assignment tool.')

    # Licenses & E911: provisioning / regulated — flag as pre/post-flight checks.
    add_result(task_id, 'Manual — Licenses', 'License & add-on parity', 'Manual',
               'Confirm the winning account has matching licenses and add-on features (e.g. Call Queue Routing Options) enabled.')
    add_result(task_id, 'Manual — E911', 'Emergency response locations', 'Manual',
               'Location definitions are recreated automatically (see the Emergency Location rows). Per-number and per-device E911 assignment and carrier address validation are regulated and must still be confirmed on the target.')