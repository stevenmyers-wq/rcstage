import io
import json
import zipfile
import pandas as pd
import time
import requests
from webapp.rc_api import rc_api_call
from webapp import task_control

migration_progress_store = {}


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
    # Preserve any accumulated per-item results across progress updates, since
    # this replaces the whole store entry on every call.
    existing = migration_progress_store.get(task_id, {})
    migration_progress_store[task_id] = {
        'current': current,
        'total': total,
        'message': message,
        'status': status,
        'results': existing.get('results', [])
    }

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
    
    for attempt in range(max_retries):
        resp = rc_api_call(
            endpoint, 
            method=method, 
            token=token, 
            json=json_payload, 
            data=data, 
            files=files, 
            params=params, 
            return_response=True 
        )
        
        status_code = getattr(resp, 'status_code', 500)
        
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
    headers = {"Authorization": f"Bearer {token}"}
    
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

# --- EXPORT LOGIC ---
def run_account_export(task_id, unbind_devices=False, token=None):
    update_progress(task_id, 2, 100, "Fetching Global Account Structure...")
    
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token, task_id)
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token, task_id)
    sites = fetch_all_pages('/restapi/v1.0/account/~/sites', token, task_id)
    
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
        "extensions_raw": extensions,
        "detailed_extensions": {},
        "custom_audio_map": []
    }
    
    zip_buffer = io.BytesIO()
    total_exts = len(extensions)
    
    # ONLY these types actually support Answering Rules and Forwarding Numbers in the RC API
    VALID_CALL_HANDLING_TYPES = [
        'User', 'Department', 'VirtualUser', 'DigitalUser', 
        'FlexibleUser', 'Voicemail', 'MessageOnly', 'Announcement', 'AnnouncementOnly'
    ]
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
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
                                try:
                                    audio_bytes, mime = download_audio_content(audio_uri, token, task_id)
                                    if audio_bytes:
                                        file_ext = 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'wav'
                                        filename = f"audio/{ext_id}_{rule_id}_{greeting['type']}.{file_ext}"
                                        zip_file.writestr(filename, audio_bytes)
                                        config_data["custom_audio_map"].append({
                                            "ext_id": ext_id,
                                            "ext_type": ext_type,
                                            "ext_name": ext_name,
                                            "rule_id": rule_id,
                                            "greeting_type": greeting['type'],
                                            "audio_id": audio_id,
                                            "filename": filename
                                        })
                                except Exception:
                                    pass

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
                        try:
                            audio_bytes, mime = download_audio_content(audio_uri, token, task_id)
                            if audio_bytes:
                                file_ext = 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'wav'
                                filename = f"audio/{ext_id}_ivr_prompt.{file_ext}"
                                zip_file.writestr(filename, audio_bytes)
                                config_data["custom_audio_map"].append({
                                    "ext_id": ext_id,
                                    "ext_type": ext_type,
                                    "ext_name": ext_name,
                                    "rule_id": "ivr_prompt",
                                    "greeting_type": "IvrPrompt",
                                    "audio_id": audio_id,
                                    "filename": filename
                                })
                        except Exception:
                            pass
            elif ext_type in ['Announcement', 'AnnouncementOnly']:
                ext_details["announcement_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)
            elif ext_type in ['MessageOnly', 'Voicemail']:
                ext_details["message_only_settings"] = safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', task_id=task_id, method='GET', token=token, raise_error=False)

            config_data["detailed_extensions"][ext_id] = ext_details
            time.sleep(0.05)

        if unbind_devices:
            for i, dev in enumerate(devices):
                update_progress(task_id, 90, 100, f"Unbinding device {dev.get('name', 'Unknown')}...")
                if 'phoneLines' in dev and len(dev['phoneLines']) > 0:
                    try:
                        safe_rc_api_call(f'/restapi/v1.0/account/~/device/{dev["id"]}', task_id=task_id, method='PUT', json_payload={"phoneLines": []}, token=token, raise_error=False)
                    except Exception:
                        pass
        
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

    update_progress(task_id, 100, 100, "Export Complete! ZIP file downloading...", status='completed')
    zip_buffer.seek(0)
    return zip_buffer

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


# --- IMPORT LOGIC ---
def run_account_import(task_id, zip_bytes, token=None):
    try:
        update_progress(task_id, 0, 100, "Extracting and Parsing ZIP...", status='running')
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_ref:
            if "config.json" not in zip_ref.namelist():
                update_progress(task_id, 0, 100, "Invalid ZIP: Missing config.json", status='error')
                return

            config = json.loads(zip_ref.read("config.json"))
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
            # Pass 10: Manual worklist — things the platform won't let us copy.
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
    devices, licenses, E911 — as explicit 'Manual' result rows so nothing is
    silently dropped and the engineer gets a post-migration checklist."""
    # Phone numbers: carrier/billing-owned, cannot move between tenants.
    numbers = config.get("phone_numbers", [])
    dids = [n for n in numbers if n.get('usageType') in ('DirectNumber', 'MainCompanyNumber', 'CompanyNumber', 'AdditionalCompanyNumber')]
    if dids:
        add_result(task_id, 'Manual — Numbers', f"{len(dids)} phone number(s)", 'Manual',
                   'Carrier-owned — cannot copy. Assign temporary ALNs at cutover, then port the real numbers and re-map with the Phone Number Assignment tool.')

    # Devices: MAC re-provisioning worklist (freed MAC -> matching user).
    dev_count = 0
    for dev in config.get("devices", []):
        serial = dev.get('serial') or dev.get('mac')
        if not serial:
            continue
        dev_count += 1
        model = (dev.get('model') or {}).get('name', 'Unknown model')
        add_result(task_id, 'Manual — Device', f"{dev.get('name', 'Device')} ({serial})", 'Manual',
                   f"{model}: release the MAC on the losing tenant, then add it to the matching user's 'Existing Phone' device on the target.")
    if not dev_count and config.get("devices"):
        add_result(task_id, 'Manual — Device', f"{len(config['devices'])} device(s)", 'Manual',
                   'Re-provision on the target account (no MAC captured for automated assignment).')

    # Licenses & E911: provisioning / regulated — flag as pre/post-flight checks.
    add_result(task_id, 'Manual — Licenses', 'License & add-on parity', 'Manual',
               'Confirm the winning account has matching licenses and add-on features (e.g. Call Queue Routing Options) enabled.')
    add_result(task_id, 'Manual — E911', 'Emergency response locations', 'Manual',
               'Emergency addresses are regulated and must be re-registered/validated per number & device on the target.')