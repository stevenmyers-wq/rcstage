import io
import json
import zipfile
import pandas as pd
import time
import requests
from webapp.rc_api import rc_api_call

migration_progress_store = {}

def update_progress(task_id, current, total, message, status='running'):
    migration_progress_store[task_id] = {
        'current': current,
        'total': total,
        'message': message,
        'status': status
    }

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
            
            old_to_new_sites = {}
            old_to_new_cost_centers = {}
            old_to_new_exts = {}

            # Pass 0: Account Level Settings & Cost Centers
            update_progress(task_id, 2, 100, "Applying Account Configs & Cost Centers...")
            if config.get("call_recording"):
                try:
                    cr_payload = {k: v for k, v in config["call_recording"].items() if k not in ['id', 'uri']}
                    safe_rc_api_call('/restapi/v1.0/account/~/call-recording', task_id=task_id, method='PUT', json_payload=cr_payload, token=token, raise_error=False)
                except Exception:
                    pass

            for cc in config.get("cost_centers", []):
                try:
                    payload = {"name": cc['name'], "billingCode": cc.get('billingCode')}
                    new_cc = safe_rc_api_call('/restapi/v1.0/account/~/cost-center', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    old_to_new_cost_centers[str(cc['id'])] = str(new_cc['id'])
                except Exception:
                    pass

            # Pass 1: Sites
            update_progress(task_id, 10, 100, "Recreating Sites...")
            for site in config.get("sites", []):
                if site['id'] == 'main-site':
                    old_to_new_sites[site['id']] = 'main-site'
                    continue
                try:
                    payload = {"name": site['name'], "extensionNumber": site.get('extensionNumber')}
                    new_site = safe_rc_api_call('/restapi/v1.0/account/~/sites', task_id=task_id, method='POST', json_payload=payload, token=token, raise_error=True)
                    old_to_new_sites[site['id']] = str(new_site['id'])
                except Exception:
                    pass

            # Pass 2: Groups (Park, Paging, Queues, IVR, Announce, Message-Only)
            update_progress(task_id, 20, 100, "Recreating Extension Structures...")
            detailed_exts = config.get("detailed_extensions", {})
            
            for old_id, details in detailed_exts.items():
                ext_type = details['base_info'].get('type')
                payload = {"extensionNumber": details['base_info'].get('extensionNumber')}
                
                if 'name' in details['base_info']: payload['name'] = details['base_info']['name']
                if 'site' in details['base_info'] and details['base_info']['site'].get('id') in old_to_new_sites:
                    payload['site'] = {"id": old_to_new_sites[details['base_info']['site']['id']]}
                if 'costCenter' in details['base_info'] and str(details['base_info']['costCenter'].get('id')) in old_to_new_cost_centers:
                    payload['costCenter'] = {"id": old_to_new_cost_centers[str(details['base_info']['costCenter']['id'])]}

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
                except Exception:
                    pass

            # Pass 3: Audio Uploads
            total_audio = len(audio_map)
            for i, a_map in enumerate(audio_map):
                update_progress(task_id, 60 + int((i/total_audio)*35), 100, f"Uploading Audio: {a_map['filename']}")
                new_ext_id = old_to_new_exts.get(str(a_map['ext_id']))
                if not new_ext_id: continue
                    
                try:
                    audio_bytes = zip_ref.read(a_map['filename'])
                    filename_clean = a_map['filename'].split('/')[-1]
                    if a_map['greeting_type'] == 'IvrPrompt':
                        files = {'attachment': (filename_clean, audio_bytes, 'audio/mpeg')}
                        prompt_res = safe_rc_api_call('/restapi/v1.0/account/~/ivr-prompts', task_id=task_id, method='POST', data={'name': filename_clean}, files=files, token=token, raise_error=True)
                        safe_rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{new_ext_id}', task_id=task_id, method='PUT', json_payload={"prompt": {"mode": "Audio", "audio": {"id": prompt_res['id']}}}, token=token, raise_error=True)
                    else:
                        metadata = {"type": a_map['greeting_type'], "answeringRule": {"id": a_map['rule_id']}}
                        files = {'json': ('request.json', json.dumps(metadata), 'application/json'), 'attachment': (filename_clean, audio_bytes, 'audio/mpeg')}
                        safe_rc_api_call(f'/restapi/v1.0/account/~/extension/{new_ext_id}/greeting', task_id=task_id, method='POST', files=files, token=token, raise_error=True)
                except Exception:
                    pass

            update_progress(task_id, 100, 100, "Migration Import Completed! Check portal for mapping details.", status='completed')

    except Exception as e:
        update_progress(task_id, 0, 100, f"Import Error: {str(e)}", status='error')