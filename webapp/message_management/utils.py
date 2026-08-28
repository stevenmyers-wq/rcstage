import io
import wave
import json
import os
import re
import csv
import zipfile
import time
import hashlib
import requests
from flask import session
# NOTE (Jun 2026): google-genai bumped from 0.3.0 → >=1.11.0. If Gemini TTS breaks,
# contact Riyaz Mohammed (riyaz.mohammed@ringcentral.com) before changing SDK usage here.
from google import genai
from google.genai import types
from webapp.rc_api import rc_api_call
from webapp import task_control

export_progress_store = {}
xlsx_upload_progress_store = {}
# Per-task setup cache so a chunked bulk upload only lists the Drive folder and
# builds the extension index once, then reuses them across chunks.
_xlsx_upload_cache = {}

DRIVE_API_FILES_URL = 'https://www.googleapis.com/drive/v3/files'

def safe_requests_get(url, headers=None, params=None, max_retries=6):
    """Wrapper for requests.get that safely handles RingCentral 429 Rate Limits."""
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers, params=params)
        
        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After')
            limit_window = resp.headers.get('X-Rate-Limit-Window')
            
            if retry_after and retry_after.isdigit() and int(retry_after) > 0:
                wait_time = int(retry_after)
            elif limit_window and limit_window.isdigit() and int(limit_window) > 0:
                wait_time = int(limit_window)
            else:
                wait_time = min(5 * (2 ** attempt), 60)
            
            time.sleep(wait_time + 1)
            continue
            
        return resp
    return resp

def fetch_target_endpoints():
    """Fetch all extensions and filter locally to avoid API query string rejections.

    Walks every page via navigation.nextPage so large accounts (>1000 extensions)
    return the complete set instead of only the first page.
    """
    valid_types = ['User', 'Department', 'IvrMenu', 'Voicemail', 'Announcement']
    filtered_records = []
    page = 1

    while True:
        response = rc_api_call(
            '/restapi/v1.0/account/~/extension',
            params={'perPage': 1000, 'page': page},
            raise_error=True
        )

        if not response or 'records' not in response:
            break

        filtered_records.extend(
            ext for ext in response['records']
            if ext.get('type') in valid_types
        )

        # Classic RC API paginates via navigation.nextPage
        if not response.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)

    return {'records': filtered_records}

def fetch_custom_greetings(ext_id, ext_info=None):
    """
    Fetch ALL active greetings. Maintains all 3 detection layers (V1 Answering Rules,
    V2 CHaF State Rules, and Custom Media Pool) with deep logging.

    ``ext_info`` lets a caller that has already fetched the extension record hand it
    in so we don't re-request GET /extension/{id}. The bulk export does exactly this
    for every endpoint, halving the extension lookups on a large archive run and
    easing the RingCentral rate-limit pressure that stalls big exports.
    """
    if ext_info is None:
        try:
            ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
        except Exception:
            ext_info = None

    if not ext_info:
        return {'status': 'Error', 'records': []}
        
    if ext_info.get('status') == 'NotActivated':
        return {'status': 'NotActivated', 'records': []}
        
    ext_type = ext_info.get('type', 'User')
    greetings_list = []
    found_combinations = set()

    baseline_types = {
        'User': {
            'business-hours-rule': ['Voicemail', 'ConnectingMessage', 'ConnectingAudio', 'HoldMusic'],
            'after-hours-rule': ['Voicemail', 'Announcement']
        },
        'Department': {
            'business-hours-rule': ['Voicemail', 'Introductory', 'ConnectingAudio', 'HoldMusic', 'InterruptPrompt'],
            'after-hours-rule': ['Voicemail', 'Announcement']
        },
        'IvrMenu': {
            'ivr': ['IvrPrompt']
        },
        'Voicemail': {
            'business-hours-rule': ['Voicemail']
        },
        'Announcement': {
            'business-hours-rule': ['Announcement']
        }
    }

    # 1. Handle IVR Menus
    if ext_type == 'IvrMenu':
        try:
            ivr_detail = rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{ext_id}', method='GET', raise_error=True)
            prompt = ivr_detail.get('prompt') if ivr_detail else None
            if prompt:
                if prompt.get('mode') == 'Audio' and prompt.get('audio'):
                    audio_info = prompt.get('audio')
                    greetings_list.append({
                        'type': 'IvrPrompt',
                        'rule_id': 'ivr',
                        'rule_name': 'IVR Menu',
                        'id': audio_info.get('id'),
                        'name': audio_info.get('name', 'IVR Audio Prompt'),
                        'is_custom': True,
                        'preset_uri': ''
                    })
                elif prompt.get('mode') == 'TextToSpeech':
                    greetings_list.append({
                        'type': 'IvrPrompt',
                        'rule_id': 'ivr',
                        'rule_name': 'IVR Menu',
                        'id': 'tts',
                        'name': prompt.get('text', 'No TTS text entered.'),
                        'is_custom': False,
                        'preset_uri': ''
                    })
        except Exception:
            pass
            
        if not greetings_list:
            greetings_list.append({
                'type': 'IvrPrompt',
                'rule_id': 'ivr',
                'rule_name': 'IVR Menu',
                'id': 'default',
                'name': 'System Factory Default Settings',
                'is_custom': False,
                'preset_uri': ''
            })
        return {'status': 'Success', 'records': greetings_list}

    # 2. Hardcode UI Bypass for User HoldMusic (API Limitation)
    if ext_type == 'User':
        greetings_list.append({
            'type': 'HoldMusic',
            'rule_id': 'business-hours-rule',
            'rule_name': 'Business Hours',
            'id': 'na',
            'name': 'N/A via API (Check Web Portal)',
            'is_custom': False,
            'preset_uri': ''
        })
        found_combinations.add(('business-hours-rule', 'HoldMusic'))

    # 3. LAYER 1: Standard Extensions / Queues (Answering Rules)
    try:
        rules_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET')
        records = rules_resp.get('records', []) if rules_resp else []
    except Exception:
        records = []

    for rule in records:
        rule_id = rule.get('id')
        if not rule_id:
            continue
        rule_name = 'Business Hours' if rule_id == 'business-hours-rule' else 'After Hours' if rule_id == 'after-hours-rule' else rule.get('name', rule_id)
        
        try:
            rule_detail = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}', method='GET')
            if not rule_detail:
                continue

            # Check standard greetings array
            greetings_array = rule_detail.get('greetings', [])
            if isinstance(greetings_array, dict):
                greetings_array = [greetings_array]
                
            for greeting in greetings_array:
                g_type = greeting.get('type')
                if not g_type:
                    continue
                    
                if 'custom' in greeting and greeting['custom'].get('id'):
                    found_combinations.add((rule_id, g_type))
                    greetings_list.append({
                        'type': g_type,
                        'rule_id': rule_id,
                        'rule_name': rule_name,
                        'id': greeting['custom']['id'],
                        'name': greeting['custom'].get('name', "Custom Audio"),
                        'is_custom': True,
                        'preset_uri': ''
                    })
                elif 'preset' in greeting and greeting['preset'].get('id'):
                    found_combinations.add((rule_id, g_type))
                    greetings_list.append({
                        'type': g_type,
                        'rule_id': rule_id,
                        'rule_name': rule_name,
                        'id': greeting['preset']['id'],
                        'preset_uri': greeting['preset'].get('uri', ''),
                        'name': greeting['preset'].get('name', "System Default"),
                        'is_custom': False
                    })

            # Check top-level holdMusic object
            hold_music_obj = rule_detail.get('holdMusic')
            if hold_music_obj and (rule_id, 'HoldMusic') not in found_combinations:
                custom_info = hold_music_obj.get('custom')
                preset_info = hold_music_obj.get('preset')
                
                if custom_info and custom_info.get('id'):
                    found_combinations.add((rule_id, 'HoldMusic'))
                    greetings_list.append({
                        'type': 'HoldMusic',
                        'rule_id': rule_id,
                        'rule_name': rule_name,
                        'id': custom_info['id'],
                        'name': custom_info.get('name', 'Custom Audio'),
                        'is_custom': True,
                        'preset_uri': custom_info.get('contentUri', '')
                    })
                elif preset_info and preset_info.get('id'):
                    found_combinations.add((rule_id, 'HoldMusic'))
                    greetings_list.append({
                        'type': 'HoldMusic',
                        'rule_id': rule_id,
                        'rule_name': rule_name,
                        'id': preset_info['id'],
                        'preset_uri': preset_info.get('uri', ''),
                        'name': preset_info.get('name', 'System Default'),
                        'is_custom': False
                    })

        except Exception as e:
            print(f"[DEBUG Rule Detail Error] ext={ext_id} rule={rule_id}: {str(e)}")

    # 4. LAYER 2: V2 CHaF State Rules Lookup
    try:
        v2_state = rc_api_call(f'/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules/work-hours', method='GET')
        if v2_state and 'holdMusic' in v2_state:
            hm = v2_state['holdMusic']
            if hm.get('effectiveGreetingType') == 'Custom' and hm.get('custom', {}).get('id'):
                greetings_list = [g for g in greetings_list if not (g['rule_id'] == 'business-hours-rule' and g['type'] == 'HoldMusic')]
                found_combinations.add(('business-hours-rule', 'HoldMusic'))
                greetings_list.append({
                    'type': 'HoldMusic',
                    'rule_id': 'business-hours-rule',
                    'rule_name': 'Business Hours',
                    'id': hm['custom']['id'],
                    'name': hm['custom'].get('name', 'Custom Audio'),
                    'is_custom': True,
                    'preset_uri': hm['custom'].get('contentUri', '')
                })
    except Exception:
        pass

    # 5. LAYER 3: Custom Media Pool Workaround
    #
    # A greeting applied via POST /extension/{id}/greeting (the path this tool and
    # account_migration use) lands in the extension's per-extension media pool. For
    # Call Queues, several background-audio prompt slots applied this way are not
    # reliably mirrored back into the v1 answering-rule `greetings` array, so LAYER 1
    # misses them and the slot would fall through to a Default backfill even though a
    # custom clip is applied. Reflect the pool's latest custom audio for any such
    # slot the endpoint actually exposes that LAYER 1/2 didn't already resolve as
    # custom. Previously this rescue existed for HoldMusic alone, which is why e.g. a
    # custom Interrupt prompt on a queue showed as Default; the slot set below adds
    # the sibling queue prompts (Interrupt, Connecting, Introductory).
    #
    # Voicemail and Announcement are intentionally excluded: they are rule-scoped,
    # come back reliably in the answering-rule array (LAYER 1), and accumulate stale
    # copies in the pool, so reading them from the pool risks surfacing a removed
    # greeting as still-applied.
    POOL_FALLBACK_TYPES = {
        'HoldMusic', 'InterruptPrompt', 'ConnectingAudio',
        'ConnectingMessage', 'Introductory',
    }
    # Dedicated single-purpose endpoints (Announcement-Only = type 'Announcement',
    # Message-Only = type 'Voicemail') keep their one greeting in the media pool and
    # do NOT reliably surface it in the v1 answering-rule array, so LAYER 1 misses it
    # and step 6 backfills it as Default even though a custom clip is applied — which
    # is why activated Announcement/Message-Only greetings were being skipped from the
    # export. The stale-copy risk that excludes these types on User/Department
    # endpoints does not apply here: a single-slot endpoint has exactly one greeting,
    # so the pool is authoritative for it. Rescue it only for those dedicated types.
    pool_fallback_types = set(POOL_FALLBACK_TYPES)
    if ext_type == 'Announcement':
        pool_fallback_types.add('Announcement')
    elif ext_type == 'Voicemail':
        pool_fallback_types.add('Voicemail')
    try:
        custom_pool_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting', method='GET')
        pool_records = custom_pool_resp.get('records', []) if custom_pool_resp else []
        # Only rescue slots this endpoint type actually exposes on business hours,
        # so the pool can never invent a greeting the endpoint doesn't have.
        expected_bh_slots = set(baseline_types.get(ext_type, {}).get('business-hours-rule', []))

        for slot_type in pool_fallback_types & expected_bh_slots:
            # Keep any custom already resolved from the answering rule (LAYER 1) or
            # the V2 state (LAYER 2); only fill Default/preset slots from the pool.
            already_custom = any(
                g['rule_id'] == 'business-hours-rule' and g['type'] == slot_type and g['is_custom']
                for g in greetings_list
            )
            if already_custom:
                continue

            candidates = [cg for cg in pool_records if cg.get('type') == slot_type and cg.get('id')]
            if slot_type == 'HoldMusic':
                # A voicemail-named clip is never genuine hold music (preserved guard).
                safe = [cg for cg in candidates if 'voicemail' not in (cg.get('name') or '').lower()]
                candidates = safe or candidates
            if not candidates:
                continue

            candidates.sort(key=lambda x: int(x['id']) if str(x.get('id')).isdigit() else 0)
            latest = candidates[-1]

            greetings_list = [g for g in greetings_list if not (g['rule_id'] == 'business-hours-rule' and g['type'] == slot_type)]
            found_combinations.add(('business-hours-rule', slot_type))
            greetings_list.append({
                'type': slot_type,
                'rule_id': 'business-hours-rule',
                'rule_name': 'Business Hours',
                'id': latest['id'],
                'name': latest.get('name', 'Custom Audio'),
                'is_custom': True,
                'preset_uri': latest.get('contentUri', '')
            })
    except Exception:
        pass

    # 6. Backfill missing base slots
    expected_matrix = baseline_types.get(ext_type, {})
    for r_id, slots in expected_matrix.items():
        r_name = 'Business Hours' if r_id == 'business-hours-rule' else 'After Hours'
        for slot in slots:
            if (r_id, slot) not in found_combinations:
                greetings_list.append({
                    'type': slot,
                    'rule_id': r_id,
                    'rule_name': r_name,
                    'id': 'default',
                    'name': 'System Factory Default Settings',
                    'is_custom': False,
                    'preset_uri': ''
                })
                
    return {'status': 'Success', 'records': greetings_list}

def download_greeting_audio(ext_id, greeting_id, is_ivr=False, is_custom=True, greeting_type=None, preset_uri=None, skip_fallback=False):
    """Fetch raw audio. Enforces strict type checking to reject cross-contaminated RingCentral API IDs."""
    
    # Check for hardcoded UI Bypass
    if str(greeting_id).lower() == 'na':
        default_text = "This audio track cannot be retrieved via the API. Please manage it directly in the RingCentral Web Portal."
        wav_buf = generate_tts_audio_bytes(default_text, voice_name="Kore")
        return wav_buf.read(), "audio/wav"

    content_uri = None
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    headers = {'Authorization': f'Bearer {token}'}

    if preset_uri and ('media' in preset_uri.lower() or 'content' in preset_uri.lower()):
        content_uri = preset_uri
    elif is_ivr:
        if not is_custom and greeting_id != 'default':
            raise Exception("Cannot stream Text-to-Speech IVR prompts directly as files.")
        meta = rc_api_call(f'/restapi/v1.0/account/~/ivr-prompts/{greeting_id}')
        content_uri = meta.get('contentUri') if meta else None
    elif is_custom and greeting_id != 'default' and not content_uri:
        try:
            meta = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting/{greeting_id}')
            if meta and isinstance(meta, dict):
                content_uri = meta.get('contentUri')
        except Exception:
            content_uri = None

    if not content_uri and not is_custom:
        dict_url = "https://platform.ringcentral.com/restapi/v1.0/dictionary/greeting"
        resp = safe_requests_get(dict_url, params={'greetingType': greeting_type}, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            records = data.get('records', [])
            
            rec = None
            if greeting_id != 'default':
                rec = next((r for r in records if str(r.get('id')) == str(greeting_id)), None)
                if rec and rec.get('type') != greeting_type:
                    rec = None
            
            if not rec and records:
                try:
                    ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
                    ext_type = ext_info.get('type') if ext_info else 'User'
                except Exception:
                    ext_type = 'User'
                    
                expected_usage = 'DepartmentExtensionAnsweringRule' if ext_type == 'Department' else 'UserExtensionAnsweringRule'

                valid_records = [
                    r for r in records 
                    if r.get('type') == greeting_type and r.get('usageType') in [expected_usage, 'ExtensionAnsweringRule']
                ]
                if not valid_records:
                    valid_records = [r for r in records if r.get('type') == greeting_type]

                target_names = ["Default", "Acoustic", "Ring tones", "Beautiful", "Corporate", "Classic"]
                
                if valid_records:
                    rec = next((r for r in valid_records if r.get('name') in target_names), valid_records[0])
                else:
                    rec = None

            if rec:
                content_uri = rec.get('contentUri')
                if not content_uri and 'uri' in rec:
                    m_resp = safe_requests_get(rec['uri'], headers=headers)
                    if m_resp.status_code == 200:
                        content_uri = m_resp.json().get('contentUri')

                if content_uri and 'mailboxId=' in content_uri:
                    content_uri = re.sub(r'mailboxId=\d+', f'mailboxId={ext_id}', content_uri)
            
    if content_uri:
        response = safe_requests_get(content_uri, headers=headers)
        if response.status_code == 200:
            return response.content, response.headers.get('Content-Type', 'audio/mpeg')
            
    if skip_fallback:
        raise Exception("No physical audio file explicitly mapped in RingCentral for export.")
        
    if greeting_type == 'HoldMusic':
        raise Exception("RingCentral's physical hold music track could not be resolved.")

    default_text = "This is the factory default system audio."
    if greeting_type == 'Voicemail':
        default_text = "Please leave your message after the tone."
    elif greeting_type == 'ConnectingMessage':
        default_text = "Please hold while I try to connect you."
    elif greeting_type == 'InterruptPrompt':
        default_text = "Thank you for your patience. Please continue to hold."
    elif greeting_type == 'Introductory':
        default_text = "Thank you for calling."
    elif greeting_type == 'Announcement':
        default_text = "Thank you for calling. Goodbye."
    
    wav_buf = generate_tts_audio_bytes(default_text, voice_name="Kore")
    return wav_buf.read(), "audio/wav"

def _normalize_prompt_name(value):
    """Lower-cased, extension-stripped key for matching IVR prompt names."""
    if not value:
        return ''
    base = str(value).strip()
    if '.' in base:
        base = base.rsplit('.', 1)[0]
    return base.lower()

def find_existing_ivr_prompts(filename, prompt_name=None):
    """
    Scan the account-wide IVR prompt library and return every record whose name
    matches the file being uploaded (case-insensitive, ignoring file extension).

    RingCentral exposes the stored name as 'filename' on library records, but we
    also check 'name' defensively in case the schema differs. Returns a list so
    the caller can compare content against all same-named candidates, since the
    library may already contain several duplicates under one name.
    """
    targets = {
        _normalize_prompt_name(filename),
        _normalize_prompt_name(prompt_name),
    }
    targets.discard('')
    if not targets:
        return []

    matches = []
    page = 1
    while True:
        resp = rc_api_call(
            '/restapi/v1.0/account/~/ivr-prompts',
            params={'perPage': 1000, 'page': page}
        )
        if not resp or 'records' not in resp:
            break

        for rec in resp['records']:
            if any(_normalize_prompt_name(rec.get(f)) in targets for f in ('filename', 'name')):
                matches.append(rec)

        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)

    return matches

def _ivr_prompt_content_matches(record, file_data):
    """
    Return True only when we can positively confirm the existing library prompt's
    audio is byte-identical to the file being uploaded. Any failure to retrieve
    the stored audio returns False, so an unverifiable prompt is treated as a
    name collision rather than silently reused.
    """
    content_uri = record.get('contentUri')
    if not content_uri:
        return False

    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    headers = {'Authorization': f'Bearer {token}'}
    resp = safe_requests_get(content_uri, headers=headers)
    if resp.status_code != 200:
        return False

    return hashlib.sha256(resp.content).digest() == hashlib.sha256(file_data).digest()

def upload_custom_greeting(ext_id, file_obj, greeting_type_str, greeting_name=None):
    ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
    ext_type = ext_info.get('type') if ext_info else None
    
    file_data = file_obj.read()
    filename = file_obj.filename
    content_type = getattr(file_obj, 'content_type', 'audio/wav')
    
    rule_id = None
    greeting_type = greeting_type_str
    if ":" in greeting_type_str:
        rule_id, greeting_type = greeting_type_str.split(":", 1)
    
    if ext_type == 'IvrMenu':
        prompt_name = greeting_name or filename.split('.')[0]

        # The /ivr-prompts endpoint is an account-wide pool, so a blind POST
        # creates a duplicate every time audio is applied. Look for prompts that
        # already share this name and decide based on their content:
        #   - identical audio  -> reuse the existing prompt (no duplicate)
        #   - different audio   -> raise, so a name clash never silently swaps the
        #                          wrong track in or piles up mismatched prompts
        existing_matches = find_existing_ivr_prompts(filename, prompt_name)
        reusable = next(
            (rec for rec in existing_matches if _ivr_prompt_content_matches(rec, file_data)),
            None
        )

        if reusable:
            prompt_res = {'id': reusable['id'], 'reused': True, 'name': reusable.get('filename')}
        elif existing_matches:
            existing_label = (
                existing_matches[0].get('filename')
                or existing_matches[0].get('name')
                or prompt_name
            )
            existing_ids = ', '.join(str(rec.get('id')) for rec in existing_matches if rec.get('id'))
            raise ValueError(
                f"An IVR prompt named '{existing_label}' already exists in the account "
                f"prompt library (id {existing_ids}) with different audio. Rename your "
                f"file, or delete/replace the existing prompt, before uploading."
            )
        else:
            data_payload = {'name': prompt_name}
            files = { 'attachment': (filename, file_data, content_type) }
            prompt_res = rc_api_call('/restapi/v1.0/account/~/ivr-prompts', method='POST', data=data_payload, files=files, raise_error=True)

        if prompt_res and 'id' in prompt_res:
            update_payload = { "prompt": { "mode": "Audio", "audio": { "id": prompt_res['id'] } } }
            rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{ext_id}', method='PUT', json=update_payload, raise_error=True)
        return prompt_res

    if not rule_id:
        rules_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET')
        if rules_resp and 'records' in rules_resp:
            bh_rule = next((r for r in rules_resp['records'] if r.get('id') == 'business-hours-rule'), None)
            if bh_rule:
                rule_id = bh_rule['id']
            elif len(rules_resp['records']) > 0:
                rule_id = rules_resp['records'][0]['id']

    if not rule_id:
        rule_id = 'business-hours-rule'

    # BYPASS: Prevent CMN-101 bind errors for User HoldMusic
    if ext_type == 'User' and greeting_type == 'HoldMusic':
        files_standard = {
            'json': ('request.json', json.dumps({"type": greeting_type}), 'application/json'),
            'attachment': (filename, file_data, content_type)
        }
        return rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/greeting',
            method='POST',
            files=files_standard,
            raise_error=True
        )

    # Attempt 1: Atomic Upload + Bind.
    # The answeringRule id is what actually assigns the audio to the rule, so it
    # MUST be present in the JSON body for a rule-scoped greeting (Voicemail,
    # Announcement, Connecting*, Introductory, InterruptPrompt). RingCentral
    # rejects e.g. a User Voicemail greeting that is posted without it.
    metadata = {
        "type": greeting_type,
        "answeringRule": {"id": rule_id}
    }
    files = {
        'json': ('request.json', json.dumps(metadata), 'application/json'),
        'attachment': (filename, file_data, content_type)
    }

    try:
        greeting_result = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/greeting?apply=true',
            method='POST',
            files=files,
            raise_error=True
        )
        return greeting_result

    except Exception as e:
        err_str = str(e)
        # Suppress atomic error log if it's the expected API limitation format
        if not (greeting_type == 'HoldMusic' and 'invalid' in err_str):
            print(f"[DEBUG UPLOAD ATOMIC ERROR] failed for ext={ext_id} type={greeting_type}: {err_str}")

        if greeting_type == 'HoldMusic':
            # Hold music is not a rule "greeting"; it lives in the rule's holdMusic
            # object. Upload the media to the pool, then bind it separately. CMN-101
            # here just means the account manages hold music elsewhere, so it's
            # swallowed and the pooled upload is still returned.
            files_fallback = {
                'json': ('request.json', json.dumps({"type": greeting_type}), 'application/json'),
                'attachment': (filename, file_data, content_type)
            }
            greeting_result = rc_api_call(
                f'/restapi/v1.0/account/~/extension/{ext_id}/greeting',
                method='POST',
                files=files_fallback,
                raise_error=True
            )
            audio_id = greeting_result.get('id')
            if audio_id:
                try:
                    v1_payload = {"holdMusic": {"effectiveGreetingType": "Custom", "custom": {"id": audio_id}}}
                    rc_api_call(
                        f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}',
                        method='PUT',
                        json=v1_payload,
                        raise_error=True
                    )
                except Exception as e1:
                    # Completely suppress CMN-101 printout
                    if 'CMN-101' not in str(e1):
                        print(f"[DEBUG V1 BIND ERROR] ext={ext_id}: {str(e1)}")
            return greeting_result

        # Attempt 2: some accounts reject the apply=true flag on this endpoint;
        # retry the identical create+apply POST without it. The answeringRule
        # binding stays in the body so the greeting is still assigned to the rule
        # in a single call. This matches the proven upload path in account_migration
        # and fixes the "upload user VM greeting" API error, where the old fallback
        # dropped answeringRule and RingCentral rejected the rule-scoped greeting.
        return rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/greeting',
            method='POST',
            files=files,
            raise_error=True
        )

def generate_tts_audio_bytes(text, voice_name="Kore", style="professional and clear"):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment.")
        
    client = genai.Client(api_key=api_key)
    tts_prompt = f"Voice instruction: You MUST speak with a clear Australian English accent and a {style} style. Do NOT include any breathing sounds, sighs, gasps, or audio artefacts. Deliver the text naturally and smoothly.\nText to speak: {text}"
    
    response = client.models.generate_content(
        model='gemini-3.1-flash-tts-preview',
        contents=tts_prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            )
        )
    )
    pcm_data = response.candidates[0].content.parts[0].inline_data.data
    
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_data)
    
    wav_buffer.seek(0)
    return wav_buffer

def transcribe_audio_bytes(audio_bytes, mime_type='audio/wav'):
    """Speech-to-text for a greeting clip via Gemini (same SDK/key already used for
    TTS in this module, so no extra credentials or codec handling are needed).

    Returns the spoken text, or '' when the clip has no intelligible speech (music,
    silence, ring tones). Raises on a hard API/config error so the export can record
    the failure against that one row without aborting the whole archive.
    """
    if not audio_bytes:
        return ''

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment.")

    client = genai.Client(api_key=api_key)
    prompt = (
        "Transcribe the spoken words in this audio clip verbatim. "
        "Return only the transcription text with no commentary, labels, or quotation marks. "
        "If there is no intelligible speech (for example music, a ring tone, or silence), "
        "return nothing at all."
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[
            prompt,
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type or 'audio/wav'),
        ],
    )
    return (getattr(response, 'text', '') or '').strip()

def bulk_export_greetings(ext_ids, task_id=None, ignore_defaults=False, transcribe=False):
    zip_buffer = io.BytesIO()
    csv_data = io.StringIO()
    csv_writer = csv.writer(csv_data)
    header = ['Endpoint Name', 'Extension Number', 'Endpoint Type', 'Rule / Context', 'Greeting Type', 'Source', 'Exported Filename', 'Original Text / Details']
    if transcribe:
        header.append('Transcription (STT)')
    csv_writer.writerow(header)

    total = len(ext_ids)
    if task_id:
        export_progress_store[task_id] = {'current': 0, 'total': total}

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for i, ext_id in enumerate(ext_ids):
          # One endpoint must never sink the whole batch. Everything for a single
          # endpoint (its lookups, greeting scan and downloads) is wrapped so an
          # unexpected error is recorded as a row and the export moves on, instead
          # of raising out of the route and failing all endpoints in the batch with
          # an opaque HTTP 500.
          try:
            ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
            if not ext_info: continue

            ext_name = ext_info.get('name', 'Unknown')
            ext_num = ext_info.get('extensionNumber', ext_id)
            ext_type = ext_info.get('type', 'Unknown')

            safe_ext_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', ext_name).strip()

            # Reuse the record we just fetched instead of letting fetch_custom_greetings
            # request GET /extension/{id} a second time for the same endpoint.
            greetings = fetch_custom_greetings(ext_id, ext_info=ext_info)
            if greetings.get('status') == 'Success' and 'records' in greetings:
                for g in greetings['records']:
                    if ignore_defaults and not g['is_custom']:
                        continue
                    
                    if g['id'] == 'default' or g.get('name') == 'None':
                        continue

                    is_custom = g['is_custom']
                    is_ivr = g['type'] == 'IvrPrompt'
                    g_id = g['id']
                    g_type = g['type']
                    rule_name = g.get('rule_name', 'IVR Menu')
                    safe_rule = re.sub(r'[^a-zA-Z0-9_\- ]', '', rule_name).strip()
                    g_text = g.get('name', '')
                    
                    export_filename = ""
                    source = ""
                    transcription = ""

                    if is_custom:
                        source = "Custom Audio"
                    elif g_id == 'tts':
                        source = "TTS String"
                    else:
                        source = "System Preset"

                    if g_id not in ['tts']:
                        try:
                            audio_bytes, mime = download_greeting_audio(
                                ext_id, g_id, is_ivr=is_ivr, is_custom=is_custom,
                                greeting_type=g_type, preset_uri=g.get('preset_uri'),
                                skip_fallback=True
                            )
                            file_ext = 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'wav'
                            export_filename = f"[{ext_num}] {safe_ext_name} - {safe_rule} - {g_type}.{file_ext}"
                            zip_file.writestr(export_filename, audio_bytes)

                            # Optional speech-to-text of the clip we just downloaded.
                            # A transcription failure must never sink the export, so it
                            # is recorded per-row and we move on.
                            if transcribe:
                                try:
                                    transcription = transcribe_audio_bytes(audio_bytes, mime)
                                except Exception as te:
                                    transcription = f"ERROR: {str(te)}"

                            time.sleep(0.5)

                        except Exception as e:
                            export_filename = f"ERROR: {str(e)}"
                    else:
                        export_filename = "N/A (Text-To-Speech String)"
                        # The spoken content of a TTS greeting is already known text,
                        # so surface it in the transcription column too for a complete
                        # "what does this say" view.
                        transcription = g_text

                    row = [ext_name, ext_num, ext_type, rule_name, g_type, source, export_filename, g_text]
                    if transcribe:
                        row.append(transcription)
                    csv_writer.writerow(row)
          except Exception as ext_err:
              # Record the endpoint-level failure in the audit and keep going.
              err_row = ['', ext_id, '', '', '', 'ERROR',
                         f"ERROR processing endpoint: {str(ext_err)}", '']
              if transcribe:
                  err_row.append('')
              csv_writer.writerow(err_row)
          finally:
              if task_id:
                  export_progress_store[task_id]['current'] = i + 1

        zip_file.writestr("Greeting_Mapping_Audit.csv", csv_data.getvalue())

    zip_buffer.seek(0)
    return zip_buffer

# ---------------------------------------------------------------------------
# Bulk upload from a spreadsheet + a public Google Drive folder
# ---------------------------------------------------------------------------
# The spreadsheet maps EXT NUMBER | GREETING TYPE | GREETING NAME. Each row's
# GREETING NAME is matched (by filename) against the audio files found in the
# supplied Drive folder, then applied to the extension via upload_custom_greeting.

class _MemoryUpload:
    """Minimal stand-in for a Werkzeug FileStorage so downloaded Drive bytes can
    flow through upload_custom_greeting unchanged (it only needs .read(),
    .filename and .content_type)."""
    def __init__(self, data, filename, content_type='audio/wav'):
        self._buf = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type

    def read(self):
        return self._buf.read()

def _drive_api_key():
    return os.environ.get('GOOGLE_DRIVE_API_KEY') or os.environ.get('GEMINI_API_KEY')

def _drive_auth(access_token):
    """Resolve how to authorize a Drive API call. A per-user OAuth access token
    (Bearer) is preferred so the request runs as the signed-in user and can reach
    the private folders they can see; otherwise fall back to the app API key,
    which only reaches publicly shared content."""
    if access_token:
        return {'Authorization': f'Bearer {access_token}'}, {}
    api_key = _drive_api_key()
    if not api_key:
        raise ValueError(
            "No Google Drive authorization available. Grant Drive access when "
            "prompted, or configure GOOGLE_DRIVE_API_KEY for public-folder access."
        )
    return {}, {'key': api_key}

def extract_drive_folder_id(url):
    """Pull a Google Drive folder ID out of the various link formats users paste,
    or accept a bare ID."""
    if not url:
        return None
    raw = str(url).strip()

    for pattern in (
        r'/folders/([A-Za-z0-9_-]+)',
        r'[?&]id=([A-Za-z0-9_-]+)',
    ):
        match = re.search(pattern, raw)
        if match:
            return match.group(1)

    # Bare folder ID pasted directly.
    if re.fullmatch(r'[A-Za-z0-9_-]{20,}', raw):
        return raw

    return None

def list_drive_folder_files(folder_id, access_token=None):
    """List non-folder files in a Drive folder via the Drive API, authorized as
    the signed-in user (Bearer token) or, failing that, the app API key."""
    headers, auth_params = _drive_auth(access_token)

    files = []
    page_token = None
    while True:
        params = {
            'q': f"'{folder_id}' in parents and trashed=false",
            'fields': 'nextPageToken, files(id, name, mimeType)',
            'pageSize': 1000,
            'supportsAllDrives': 'true',
            'includeItemsFromAllDrives': 'true',
        }
        params.update(auth_params)
        if page_token:
            params['pageToken'] = page_token

        resp = safe_requests_get(DRIVE_API_FILES_URL, params=params, headers=headers or None)
        if resp.status_code != 200:
            raise ValueError(
                f"Could not list the Google Drive folder (HTTP {resp.status_code}). "
                "Confirm the link is a folder link and that you have access to it "
                "in Google Drive (and granted Drive permission when prompted). "
                f"Detail: {resp.text[:200]}"
            )

        data = resp.json()
        files.extend(data.get('files', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            break

    return files

def download_drive_file_bytes(file_id, access_token=None):
    """Download raw bytes of a Drive file, authorized as the signed-in user
    (Bearer token) or the app API key."""
    headers, auth_params = _drive_auth(access_token)
    params = {'alt': 'media', 'supportsAllDrives': 'true'}
    params.update(auth_params)

    resp = safe_requests_get(
        f"{DRIVE_API_FILES_URL}/{file_id}",
        params=params,
        headers=headers or None
    )
    if resp.status_code == 200:
        return resp.content
    if resp.status_code == 401:
        # Not a rate limit: the per-user Drive OAuth token has expired (these
        # last about an hour and cannot be refreshed server-side). The chunked
        # upload refreshes the token between chunks, so this should be rare.
        raise ValueError(
            "Google Drive authorization expired (HTTP 401) — this is a token "
            "timeout, not a rate limit. Re-run the upload to reauthorize."
        )
    raise ValueError(f"Failed to download Drive file (HTTP {resp.status_code}).")

def _drive_name_index(files):
    """Map file names (full and extension-stripped, lower-cased) to Drive records
    so a spreadsheet GREETING NAME can be matched with or without its extension."""
    index = {}
    for f in files:
        if f.get('mimeType') == 'application/vnd.google-apps.folder':
            continue
        name = f.get('name') or ''
        keys = {name.strip().lower(), name.rsplit('.', 1)[0].strip().lower()}
        for key in keys:
            if key and key not in index:
                index[key] = f
    return index

def _match_drive_file(name_index, greeting_name):
    raw = str(greeting_name or '').strip()
    for key in (raw.lower(), raw.rsplit('.', 1)[0].strip().lower()):
        if key and key in name_index:
            return name_index[key]
    return None

def _content_type_for(filename):
    return 'audio/mpeg' if str(filename).lower().endswith('.mp3') else 'audio/wav'

def resolve_greeting_type_label(label, ext_type):
    """
    Translate a friendly spreadsheet greeting-type label into the internal
    'rule_id:GreetingType' code, using the endpoint's actual object type to
    disambiguate. Raises ValueError when the label can't be understood.
    """
    raw = str(label or '').strip()
    if not raw:
        raise ValueError("Greeting Type is blank")

    # Collapse dashes (-, en/em dashes), slashes and underscores to spaces.
    norm = re.sub(r'[\s\-‐-―_/]+', ' ', raw.lower()).strip()

    # Single-slot object types are unambiguous regardless of how the label reads.
    if ext_type == 'IvrMenu':
        return 'ivr:IvrPrompt'
    if ext_type == 'Announcement':
        return 'business-hours-rule:Announcement'
    if ext_type == 'Voicemail':
        return 'business-hours-rule:Voicemail'

    # Answering-rule context (defaults to business hours).
    rule_id = 'after-hours-rule' if 'after hours' in norm else 'business-hours-rule'

    # Greeting slot. Order matters: check the more specific phrases first.
    if 'connecting message' in norm:
        g_type = 'ConnectingMessage'
    elif 'connecting' in norm:
        g_type = 'ConnectingAudio'
    elif 'hold' in norm:
        g_type = 'HoldMusic'
    elif 'intro' in norm:
        g_type = 'Introductory'
    elif 'interrupt' in norm:
        g_type = 'InterruptPrompt'
    elif 'announcement' in norm:
        g_type = 'Announcement'
    elif 'voicemail' in norm or 'voice mail' in norm:
        g_type = 'Voicemail'
    else:
        raise ValueError(f"Unrecognized greeting type '{raw}' for a {ext_type} endpoint")

    return f"{rule_id}:{g_type}"

def build_extension_number_index():
    """Map extension number -> extension record across every page of the account
    directory, so spreadsheet rows can resolve a number to an internal id/type."""
    index = {}
    page = 1
    while True:
        resp = rc_api_call(
            '/restapi/v1.0/account/~/extension',
            params={'perPage': 1000, 'page': page},
            raise_error=True
        )
        if not resp or 'records' not in resp:
            break
        for ext in resp['records']:
            num = str(ext.get('extensionNumber') or '').strip()
            if num and num not in index:
                index[num] = ext
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)
    return index

def _cell_str(value):
    """Stringify a spreadsheet cell, dropping NaN and rendering whole-number
    floats (e.g. an ext number read as 10118.0) without the trailing '.0'."""
    if value is None:
        return ''
    if isinstance(value, float):
        if value != value:  # NaN
            return ''
        if value.is_integer():
            return str(int(value))
    return str(value).strip()

def _resolve_upload_columns(df):
    def norm(col):
        return re.sub(r'[^a-z0-9]', '', str(col).lower())

    col_map = {norm(c): c for c in df.columns}

    def find(aliases):
        for alias in aliases:
            if alias in col_map:
                return col_map[alias]
        return None

    ext_col = find(['extnumber', 'extensionnumber', 'extension', 'ext', 'number'])
    type_col = find(['greetingtype', 'type'])
    name_col = find(['greetingname', 'name', 'filename', 'file', 'audiofile', 'audio'])
    # Optional columns. TTS carries the text to synthesise for a row that uses AI
    # generation instead of a Drive file; VOICE optionally overrides the voice.
    # Both are absent on legacy (Drive-only) sheets, so neither is required.
    tts_col = find(['tts', 'ttstext', 'ttsscript', 'aitext', 'aiscript',
                    'aigeneration', 'texttospeech', 'greetingtext'])
    voice_col = find(['voice', 'aivoice', 'voiceactor', 'ttsvoice'])

    missing = [
        label for label, col in
        [('EXT NUMBER', ext_col), ('GREETING TYPE', type_col), ('GREETING NAME', name_col)]
        if not col
    ]
    if missing:
        raise ValueError(f"Spreadsheet is missing required column(s): {', '.join(missing)}")

    return ext_col, type_col, name_col, tts_col, voice_col

def _prune_xlsx_cache(max_age_seconds=7200):
    """Drop stale per-task setup so abandoned uploads don't leak memory."""
    now = time.time()
    for key in [k for k, v in _xlsx_upload_cache.items()
                if now - v.get('created', now) > max_age_seconds]:
        _xlsx_upload_cache.pop(key, None)
        xlsx_upload_progress_store.pop(key, None)


def _prepare_xlsx_upload(file_storage, drive_url, access_token, sheet_name=None):
    """One-time setup for a bulk upload: parse the sheet, list the Drive folder,
    and build the extension index. Expensive, so it's cached per task and reused
    across chunks."""
    import pandas as pd

    df = pd.read_excel(file_storage, sheet_name=(sheet_name or 0), engine='openpyxl').dropna(how='all')
    ext_col, type_col, name_col, tts_col, voice_col = _resolve_upload_columns(df)

    rows = df.to_dict('records')

    # A row applies audio one of two ways: a GREETING NAME (a Drive file) or TTS
    # (AI-generated text). Only rows that fill one of those are uploadable, so the
    # progress total counts those alone — blank rows (e.g. the unfilled slots in a
    # pre-populated "Download Selected" template) must not inflate the total or the
    # bar would stop short of 100%.
    any_drive_row = any(_cell_str(row.get(name_col)) for row in rows)
    total = sum(
        1 for row in rows
        if _cell_str(row.get(name_col)) or (tts_col and _cell_str(row.get(tts_col)))
    )

    # The Drive folder is only needed when at least one row names a file. A
    # TTS-only sheet can run without a Drive link or authorization at all.
    name_index = {}
    drive_configured = False
    if any_drive_row:
        # A link that's present but unreadable is a mistake worth surfacing loudly.
        # A link left blank while some rows still reference files is not fatal: the
        # TTS rows can proceed, and each Drive-name row fails individually with a
        # clear "needs a Drive folder link" message (see the per-row branch below).
        folder_id = extract_drive_folder_id(drive_url) if drive_url else None
        if drive_url and not folder_id:
            raise ValueError("Could not read a Google Drive folder ID from the provided link.")
        if folder_id:
            drive_files = list_drive_folder_files(folder_id, access_token=access_token)
            if not drive_files:
                raise ValueError("No files were found in that Drive folder.")
            name_index = _drive_name_index(drive_files)
            drive_configured = True

    return {
        'rows': rows,
        'ext_col': ext_col,
        'type_col': type_col,
        'name_col': name_col,
        'tts_col': tts_col,
        'voice_col': voice_col,
        'name_index': name_index,
        'drive_configured': drive_configured,
        'ext_index': build_extension_number_index(),
        'total': total,
        'created': time.time(),
    }


def xlsx_bulk_upload(file_storage, drive_url, task_id=None, access_token=None,
                     cursor=0, chunk_size=None, sheet_name=None):
    """
    Drive a bulk greeting upload from an uploaded spreadsheet plus a Drive folder
    link. Each row (EXT NUMBER, GREETING TYPE, GREETING NAME) is resolved, matched
    to a Drive file by name, downloaded and applied. Drive is read as the signed-in
    user when access_token is supplied. Every row is handled independently so one
    bad row never aborts the batch.

    When ``chunk_size`` is given the call processes only the rows starting at
    ``cursor`` and returns a ``next_cursor``/``done`` pair so the caller can loop,
    handing in a freshly refreshed Drive token each chunk. This keeps any single
    request well inside the ~1h OAuth token lifetime, which is what previously
    caused a wave of HTTP 401s partway through large batches. With ``chunk_size``
    omitted the whole sheet is processed in one pass (legacy behaviour).
    """
    chunked = chunk_size is not None

    ctx = _xlsx_upload_cache.get(task_id) if (chunked and task_id) else None
    if ctx is None:
        ctx = _prepare_xlsx_upload(file_storage, drive_url, access_token, sheet_name)
        if chunked and task_id:
            _prune_xlsx_cache()
            _xlsx_upload_cache[task_id] = ctx

    rows = ctx['rows']
    ext_col, type_col, name_col = ctx['ext_col'], ctx['type_col'], ctx['name_col']
    tts_col, voice_col = ctx.get('tts_col'), ctx.get('voice_col')
    name_index, ext_index, total = ctx['name_index'], ctx['ext_index'], ctx['total']
    drive_configured = ctx.get('drive_configured', False)

    if task_id and task_id not in xlsx_upload_progress_store:
        xlsx_upload_progress_store[task_id] = {'current': 0, 'total': total, 'logs': []}
    store = xlsx_upload_progress_store.get(task_id) if task_id else None

    # Rows already logged in earlier chunks; this call appends to the same list.
    chunk_start_len = len(store['logs']) if store is not None else 0

    def log(msg, status):
        entry = {'msg': msg, 'status': status}
        if store is not None:
            store['logs'].append(entry)

    start = cursor if chunked else 0
    end = len(rows)
    next_cursor = end
    processed_in_chunk = 0
    cancelled = False
    i = start
    while i < end:
        if chunked and processed_in_chunk >= chunk_size:
            next_cursor = i
            break
        # Cooperative stop: greetings already applied stand; remaining rows are
        # skipped. Checked here so a stop lands even mid-chunk, and the client's
        # own loop also stops calling for the next chunk.
        if task_control.is_stopped(task_id):
            cancelled = True
            next_cursor = i
            break
        row = rows[i]
        i += 1

        ext_num = _cell_str(row.get(ext_col))
        type_label = _cell_str(row.get(type_col))
        greeting_name = _cell_str(row.get(name_col))
        tts_text = _cell_str(row.get(tts_col)) if tts_col else ''
        voice = (_cell_str(row.get(voice_col)) if voice_col else '') or 'Kore'

        # Each row applies audio one of two ways: a GREETING NAME (a Drive file)
        # or TTS (AI-generated text). A row that fills neither has nothing to
        # apply, so skip it silently — this covers fully blank rows and the
        # pre-populated template slots a user intentionally left unfilled. Kept in
        # sync with the `total` count in _prepare_xlsx_upload, which keys off the
        # same two columns.
        if not greeting_name and not tts_text:
            continue

        row_label = f"Ext {ext_num or '?'} / {type_label or '?'}"
        try:
            # A row must pick exactly one method. Filling both is ambiguous, so
            # fail it rather than silently guessing which the user meant.
            if greeting_name and tts_text:
                raise ValueError(
                    "Row has both a GREETING NAME and TTS text — provide only one "
                    "(a Drive file name or TTS text), not both."
                )

            ext = ext_index.get(ext_num)
            if not ext:
                raise ValueError(f"Extension number {ext_num} not found in account")

            code = resolve_greeting_type_label(type_label, ext.get('type'))

            if tts_text:
                audio_buffer = generate_tts_audio_bytes(tts_text, voice_name=voice)
                clip_name = f"TTS_{ext_num}_{code.split(':')[-1]}.wav"
                upload_obj = _MemoryUpload(audio_buffer.read(), clip_name, 'audio/wav')
                result = upload_custom_greeting(ext['id'], upload_obj, code, greeting_name=clip_name)
                log(f"{row_label}: generated TTS ({voice}) → {ext.get('name', ext_num)}", 'success')
            else:
                if not drive_configured:
                    raise ValueError(
                        f"'{greeting_name}' needs a Google Drive folder link to be applied."
                    )
                drive_file = _match_drive_file(name_index, greeting_name)
                if not drive_file:
                    raise ValueError(f"'{greeting_name}' not found in the Drive folder")

                audio_bytes = download_drive_file_bytes(drive_file['id'], access_token=access_token)
                upload_obj = _MemoryUpload(
                    audio_bytes, drive_file['name'], _content_type_for(drive_file['name'])
                )
                result = upload_custom_greeting(ext['id'], upload_obj, code, greeting_name=greeting_name)

                reused = isinstance(result, dict) and result.get('reused')
                verb = 'reused existing prompt for' if reused else 'applied'
                log(f"{row_label}: {verb} '{drive_file['name']}' → {ext.get('name', ext_num)}", 'success')
        except Exception as e:
            log(f"{row_label}: {str(e)}", 'error')
        finally:
            processed_in_chunk += 1
            if store is not None:
                store['current'] = store.get('current', 0) + 1
            time.sleep(0.2)

    # A stop ends the whole job, so treat it as done regardless of cursor.
    done = cancelled or (not chunked) or (next_cursor >= end)

    all_logs = store['logs'] if store is not None else []
    chunk_logs = all_logs[chunk_start_len:]
    success_count = sum(1 for r in all_logs if r['status'] == 'success')
    error_count = sum(1 for r in all_logs if r['status'] == 'error')

    if done and task_id:
        _xlsx_upload_cache.pop(task_id, None)
        task_control.clear(task_id)

    # ``results`` carries the full cumulative log so the final report is complete;
    # ``chunk_results`` is just this call's rows.
    return {
        'results': all_logs,
        'chunk_results': chunk_logs,
        'success_count': success_count,
        'error_count': error_count,
        'total': total,
        'current': store['current'] if store is not None else success_count + error_count,
        'next_cursor': next_cursor,
        'done': done,
        'cancelled': cancelled,
    }

# Every greeting slot each endpoint type exposes, as friendly labels prefixed by
# the endpoint type (CQ, User, IVR, …) so a generated sheet reads unambiguously
# and drops straight into the bulk-upload flow. resolve_greeting_type_label()
# ignores the prefix and keys on the endpoint type plus the slot words, so these
# labels stay purely cosmetic — rename freely as long as the slot words survive.
TEMPLATE_SLOTS_BY_TYPE = {
    'User': [
        'User - Business Hours - Voicemail',
        'User - After Hours - Voicemail',
        'User - Business Hours - Connecting Message',
        'User - Business Hours - Connecting Audio',
        'User - Business Hours - Hold Music',
        'User - After Hours - Announcement',
    ],
    'Department': [
        'CQ - Business Hours - Voicemail',
        'CQ - After Hours - Voicemail',
        'CQ - Business Hours - Introductory Greeting',
        'CQ - Business Hours - Connecting Audio',
        'CQ - Business Hours - Hold Music',
        'CQ - Business Hours - Interrupt Prompt',
        'CQ - After Hours - Announcement',
    ],
    'IvrMenu': ['IVR - Audio Prompt'],
    'Voicemail': ['Message - Voicemail Greeting'],
    'Announcement': ['Announcement - Greeting'],
}

# Flat dropdown list backing the GREETING TYPE column, derived from the slot map
# above (in endpoint-type order) so the two never drift.
ACCEPTED_TYPE_LABELS = [
    label
    for ext_type in ('User', 'Department', 'IvrMenu', 'Voicemail', 'Announcement')
    for label in TEMPLATE_SLOTS_BY_TYPE[ext_type]
]

# AI voices offered across the AI Studio picker, the inline preview player and the
# XLSX VOICE column — one source of truth so the three stay in sync. Every entry is
# a Gemini prebuilt TTS voice; the module forces an Australian accent at synthesis
# time (see generate_tts_audio_bytes), so `tone` describes delivery, not accent.
# The first entry is the default applied when a VOICE cell is left blank.
VOICE_CATALOG = [
    # The original four, kept first and with their established labels for continuity.
    {'name': 'Kore',   'label': 'Kore (Firm Female, AU)',   'tone': 'Firm'},
    {'name': 'Aoede',  'label': 'Aoede (Warm Female, AU)',  'tone': 'Breezy'},
    {'name': 'Charon', 'label': 'Charon (Calm Male, AU)',   'tone': 'Informative'},
    {'name': 'Puck',   'label': 'Puck (Upbeat Male, AU)',   'tone': 'Upbeat'},
    # The rest of the Gemini prebuilt voice set, labelled by their documented tone.
    {'name': 'Zephyr',        'label': 'Zephyr (Bright, AU)',        'tone': 'Bright'},
    {'name': 'Fenrir',        'label': 'Fenrir (Excitable, AU)',     'tone': 'Excitable'},
    {'name': 'Leda',          'label': 'Leda (Youthful, AU)',        'tone': 'Youthful'},
    {'name': 'Orus',          'label': 'Orus (Firm, AU)',            'tone': 'Firm'},
    {'name': 'Callirrhoe',    'label': 'Callirrhoe (Easy-going, AU)','tone': 'Easy-going'},
    {'name': 'Autonoe',       'label': 'Autonoe (Bright, AU)',       'tone': 'Bright'},
    {'name': 'Enceladus',     'label': 'Enceladus (Breathy, AU)',    'tone': 'Breathy'},
    {'name': 'Iapetus',       'label': 'Iapetus (Clear, AU)',        'tone': 'Clear'},
    {'name': 'Umbriel',       'label': 'Umbriel (Easy-going, AU)',   'tone': 'Easy-going'},
    {'name': 'Algieba',       'label': 'Algieba (Smooth, AU)',       'tone': 'Smooth'},
    {'name': 'Despina',       'label': 'Despina (Smooth, AU)',       'tone': 'Smooth'},
    {'name': 'Erinome',       'label': 'Erinome (Clear, AU)',        'tone': 'Clear'},
    {'name': 'Algenib',       'label': 'Algenib (Gravelly, AU)',     'tone': 'Gravelly'},
    {'name': 'Rasalgethi',    'label': 'Rasalgethi (Informative, AU)','tone': 'Informative'},
    {'name': 'Laomedeia',     'label': 'Laomedeia (Upbeat, AU)',     'tone': 'Upbeat'},
    {'name': 'Achernar',      'label': 'Achernar (Soft, AU)',        'tone': 'Soft'},
    {'name': 'Alnilam',       'label': 'Alnilam (Firm, AU)',         'tone': 'Firm'},
    {'name': 'Schedar',       'label': 'Schedar (Even, AU)',         'tone': 'Even'},
    {'name': 'Gacrux',        'label': 'Gacrux (Mature, AU)',        'tone': 'Mature'},
    {'name': 'Pulcherrima',   'label': 'Pulcherrima (Forward, AU)',  'tone': 'Forward'},
    {'name': 'Achird',        'label': 'Achird (Friendly, AU)',      'tone': 'Friendly'},
    {'name': 'Zubenelgenubi', 'label': 'Zubenelgenubi (Casual, AU)', 'tone': 'Casual'},
    {'name': 'Vindemiatrix',  'label': 'Vindemiatrix (Gentle, AU)',  'tone': 'Gentle'},
    {'name': 'Sadachbia',     'label': 'Sadachbia (Lively, AU)',     'tone': 'Lively'},
    {'name': 'Sadaltager',    'label': 'Sadaltager (Knowledgeable, AU)','tone': 'Knowledgeable'},
    {'name': 'Sulafat',       'label': 'Sulafat (Warm, AU)',         'tone': 'Warm'},
]

# Flat name list, kept for the XLSX VOICE-column validation and its help text.
TTS_VOICES = [v['name'] for v in VOICE_CATALOG]
VALID_VOICE_NAMES = set(TTS_VOICES)

# A short, neutral line spoken for the inline voice previews. Kept generic so the
# same clip works as a sample for any greeting slot.
VOICE_PREVIEW_TEXT = "Hi, thanks for calling. Your call is important to us, and we'll be with you shortly."

# Previews are identical for a given voice, so synthesise each one once and reuse it.
# Bounded implicitly by the fixed voice catalog (at most one clip per voice).
_voice_preview_cache = {}

def generate_voice_preview_bytes(voice_name):
    """Return a cached WAV sample of VOICE_PREVIEW_TEXT spoken in `voice_name`.

    Used by the inline preview player in the AI Studio voice picker so users can
    audition a voice before committing it to a bulk generation."""
    if voice_name not in VALID_VOICE_NAMES:
        raise ValueError(f"Unknown voice '{voice_name}'.")

    cached = _voice_preview_cache.get(voice_name)
    if cached is not None:
        return io.BytesIO(cached)

    buf = generate_tts_audio_bytes(VOICE_PREVIEW_TEXT, voice_name=voice_name)
    data = buf.getvalue()
    _voice_preview_cache[voice_name] = data
    return io.BytesIO(data)

# Friendly object-type label for the generated sheet's read-only TYPE column.
# Matches the on-screen table's display names.
DISPLAY_TYPE_BY_TYPE = {
    'User': 'User',
    'Department': 'Call Queue',
    'IvrMenu': 'IVR Menu',
    'Voicemail': 'Message Only',
    'Announcement': 'Announcement Only',
}


def _reference_sheet_df():
    """The per-endpoint-type cheat sheet written to the 'Accepted Types' tab."""
    import pandas as pd
    return pd.DataFrame([
        {'Endpoint Type': 'HOW TO FILL A ROW',
         'Accepted GREETING TYPE labels':
            'Give each row ONE audio source: either GREETING NAME (a file name that '
            'matches a clip in your Drive folder) OR TTS (text to synthesise with AI). '
            'Filling both on the same row is an error. VOICE is optional and only '
            f'applies to TTS rows (defaults to {TTS_VOICES[0]}; choices: '
            f'{", ".join(TTS_VOICES)}). Leave both blank to skip a slot.'},
        {'Endpoint Type': 'User',
         'Accepted GREETING TYPE labels': '; '.join(TEMPLATE_SLOTS_BY_TYPE['User'])},
        {'Endpoint Type': 'Call Queue (Department)',
         'Accepted GREETING TYPE labels': '; '.join(TEMPLATE_SLOTS_BY_TYPE['Department'])},
        {'Endpoint Type': 'IVR Menu',
         'Accepted GREETING TYPE labels': TEMPLATE_SLOTS_BY_TYPE['IvrMenu'][0] + ' (IVR menus have a single prompt slot)'},
        {'Endpoint Type': 'Message Only',
         'Accepted GREETING TYPE labels': TEMPLATE_SLOTS_BY_TYPE['Voicemail'][0]},
        {'Endpoint Type': 'Announcement Only',
         'Accepted GREETING TYPE labels': TEMPLATE_SLOTS_BY_TYPE['Announcement'][0]},
    ])


def _write_template_workbook(buffer, upload_df):
    """Write a bulk-upload template workbook from ``upload_df`` (whose columns must
    include GREETING TYPE): the Upload sheet, a reference cheat sheet, and a hidden
    list backing a validated dropdown on the GREETING TYPE column. Shared by the
    blank starter template and the selection-driven template."""
    import pandas as pd

    columns = list(upload_df.columns)
    type_idx = columns.index('GREETING TYPE')
    # Cover every pre-filled row plus generous headroom for manual additions.
    last_data_row = max(len(upload_df) + 1, 1000)

    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        upload_df.to_excel(writer, index=False, sheet_name='Upload')
        _reference_sheet_df().to_excel(writer, index=False, sheet_name='Accepted Types')

        workbook = writer.book
        upload_ws = writer.sheets['Upload']

        # A hidden sheet holds the flat label list so the dropdown can reference a
        # range (avoiding Excel's ~255 char limit on inline list sources).
        lists_ws = workbook.add_worksheet('Lists')
        for i, label in enumerate(ACCEPTED_TYPE_LABELS):
            lists_ws.write(i, 0, label)
        lists_ws.hide()

        # Widen columns for readability.
        for idx, col in enumerate(columns):
            upload_ws.set_column(idx, idx, 14 if col == 'EXT NUMBER' else 34)

        # Dropdown on the GREETING TYPE column, from the first data row down.
        last_row = len(ACCEPTED_TYPE_LABELS)
        upload_ws.data_validation(1, type_idx, last_data_row, type_idx, {
            'validate': 'list',
            'source': f'=Lists!$A$1:$A${last_row}',
            'input_title': 'Greeting Type',
            'input_message': 'Pick an accepted label from the list.',
            'error_type': 'warning',
            'error_title': 'Unrecognized greeting type',
            'error_message': 'Not one of the accepted labels. See the "Accepted Types" tab for what applies to each endpoint type.',
        })

        # Optional dropdown on the VOICE column (AI voices), when the sheet has one.
        # The voice list lives in column B of the hidden Lists sheet.
        if 'VOICE' in columns:
            voice_idx = columns.index('VOICE')
            for i, voice in enumerate(TTS_VOICES):
                lists_ws.write(i, 1, voice)
            upload_ws.data_validation(1, voice_idx, last_data_row, voice_idx, {
                'validate': 'list',
                'source': f'=Lists!$B$1:$B${len(TTS_VOICES)}',
                'input_title': 'AI Voice',
                'input_message': f'Optional — only used for TTS rows. Defaults to {TTS_VOICES[0]}.',
                'error_type': 'warning',
                'error_title': 'Unknown voice',
                # Keep this well under Excel's 255-char limit on validation messages:
                # enumerating every voice here (30+) overflows it, and xlsxwriter then
                # silently drops the whole dropdown. Point at the list instead.
                'error_message': 'Pick a voice from the dropdown list (see the "Accepted Types" tab for the full set).',
            })


def generate_upload_template():
    """Build a downloadable .xlsx template: an Upload sheet with the required
    columns and an example row, plus a reference sheet of accepted type labels."""
    import pandas as pd

    buffer = io.BytesIO()
    upload_df = pd.DataFrame(
        [
            # Method 1: a Drive file (GREETING NAME filled, TTS blank).
            {
                'EXT NUMBER': 10118,
                'GREETING TYPE': 'CQ - Business Hours - Voicemail',
                'GREETING NAME': 'Generic_Service_Submenu.wav',
                'TTS': '',
                'VOICE': '',
            },
            # Method 2: AI text-to-speech (TTS filled, GREETING NAME blank).
            {
                'EXT NUMBER': 10119,
                'GREETING TYPE': 'CQ - Business Hours - Introductory Greeting',
                'GREETING NAME': '',
                'TTS': 'Thank you for calling. Please hold and the next available agent will be with you shortly.',
                'VOICE': 'Kore',
            },
        ],
        columns=['EXT NUMBER', 'GREETING TYPE', 'GREETING NAME', 'TTS', 'VOICE'],
    )
    _write_template_workbook(buffer, upload_df)
    buffer.seek(0)
    return buffer


def generate_selected_template(ext_ids):
    """Build a downloadable .xlsx pre-populated with one row per possible greeting
    slot for each selected endpoint, so the bulk-upload sheet arrives ready to fill
    in just the GREETING NAME column. Slots are chosen from each endpoint's object
    type (a Call Queue gets its seven slots, an IVR its single prompt, and so on).

    ``ext_ids`` preserves the caller's order; unknown ids are skipped. The endpoint
    number/name/site/type are resolved from a single account directory listing —
    the same source the on-screen table is built from. EXT NUMBER, GREETING TYPE
    and GREETING NAME drive the upload; ENDPOINT NAME, ENDPOINT TYPE and SITE are
    read-only reference columns the parser ignores."""
    import pandas as pd

    directory = fetch_target_endpoints().get('records', [])
    by_id = {str(rec.get('id')): rec for rec in directory}

    # ENDPOINT TYPE is purely informational and sits last: the upload parser binds
    # only EXT NUMBER / GREETING TYPE / GREETING NAME by header, so it's ignored on
    # re-upload regardless — trailing position just signals that at a glance.
    # GREETING NAME and TTS are the two mutually-exclusive ways to fill a slot: a
    # Drive file name, or AI text. VOICE optionally overrides the TTS voice. The
    # upload parser binds only EXT NUMBER / GREETING TYPE / GREETING NAME / TTS /
    # VOICE by header; ENDPOINT NAME / ENDPOINT TYPE / SITE are read-only reference
    # columns it ignores.
    columns = ['EXT NUMBER', 'ENDPOINT NAME', 'SITE', 'GREETING TYPE',
               'GREETING NAME', 'TTS', 'VOICE', 'ENDPOINT TYPE']
    rows = []
    for ext_id in ext_ids:
        rec = by_id.get(str(ext_id))
        if not rec:
            continue
        ext_type = rec.get('type')
        ext_num = _cell_str(rec.get('extensionNumber'))
        ext_name = rec.get('name', '')
        display_type = DISPLAY_TYPE_BY_TYPE.get(ext_type, ext_type or '')
        # Mirror the table's fallback: extensions without an explicit site sit on
        # the account's Main Site.
        site_name = (rec.get('site') or {}).get('name') or 'Main Site'
        for label in TEMPLATE_SLOTS_BY_TYPE.get(ext_type, []):
            rows.append({
                'EXT NUMBER': ext_num,
                'ENDPOINT NAME': ext_name,
                'ENDPOINT TYPE': display_type,
                'SITE': site_name,
                'GREETING TYPE': label,
                'GREETING NAME': '',
                'TTS': '',
                'VOICE': '',
            })

    if not rows:
        # Never hand back an empty sheet: leave a single blank row to fill in.
        rows.append({c: '' for c in columns})

    buffer = io.BytesIO()
    _write_template_workbook(buffer, pd.DataFrame(rows, columns=columns))
    buffer.seek(0)
    return buffer
