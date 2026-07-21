import io
import wave
import json
import os
import re
import csv
import zipfile
import time
import requests
from flask import session
# NOTE (Jun 2026): google-genai bumped from 0.3.0 → >=1.11.0. If Gemini TTS breaks,
# contact Riyaz Mohammed (riyaz.mohammed@ringcentral.com) before changing SDK usage here.
from google import genai
from google.genai import types
from webapp.rc_api import rc_api_call

export_progress_store = {}

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
    """Fetch all extensions and filter locally to avoid API query string rejections"""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    
    valid_types = ['User', 'Department', 'IvrMenu', 'Voicemail', 'Announcement']
    if response and 'records' in response:
        filtered_records = [
            ext for ext in response['records'] 
            if ext.get('type') in valid_types
        ]
        return {'records': filtered_records}
    return {'records': []}

def fetch_custom_greetings(ext_id):
    """Fetch ALL active greetings. If the extension is not activated, flags it explicitly."""
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

    # Map the exact valid slots per answering rule context to prevent phantom 'default' rows
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

    # 2. Handle Standard Extensions / Queues (Answering Rules)
    try:
        rules_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET')
        records = rules_resp.get('records', []) if rules_resp else []
    except Exception:
        records = []
        
    found_combinations = set()

    for rule in records:
        rule_id = rule.get('id')
        if not rule_id:
            continue
        rule_name = 'Business Hours' if rule_id == 'business-hours-rule' else 'After Hours' if rule_id == 'after-hours-rule' else rule.get('name', rule_id)
        
        try:
            rule_detail = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}', method='GET')
            greetings_array = rule_detail.get('greetings', []) if rule_detail else []
            if isinstance(greetings_array, dict):
                greetings_array = [greetings_array]
        except Exception:
            greetings_array = []
            
        for greeting in greetings_array:
            g_type = greeting.get('type')
            if not g_type:
                continue
                
            found_combinations.add((rule_id, g_type))
            if 'custom' in greeting and greeting['custom'].get('id'):
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
                greetings_list.append({
                    'type': g_type,
                    'rule_id': rule_id,
                    'rule_name': rule_name,
                    'id': greeting['preset']['id'],
                    'preset_uri': greeting['preset'].get('uri', ''),
                    'name': greeting['preset'].get('name', "System Default"),
                    'is_custom': False
                })

    # 3. Backfill missing base slots so the UI table is fully populated for valid extensions
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
    """Fetch raw audio utilizing safe_requests_get to prevent Rate Limit crashes."""
    content_uri = None
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    headers = {'Authorization': f'Bearer {token}'}

    if is_ivr:
        if not is_custom and greeting_id != 'default':
            raise Exception("Cannot stream Text-to-Speech IVR prompts directly as files.")
        meta = rc_api_call(f'/restapi/v1.0/account/~/ivr-prompts/{greeting_id}')
        content_uri = meta.get('contentUri') if meta else None
    else:
        if is_custom:
            meta = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting/{greeting_id}')
            content_uri = meta.get('contentUri') if meta else None
        else:
            ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
            ext_type = ext_info.get('type') if ext_info else 'User'
            expected_usage = 'DepartmentExtensionAnsweringRule' if ext_type == 'Department' else 'UserExtensionAnsweringRule'

            dict_url = "https://platform.ringcentral.com/restapi/v1.0/dictionary/greeting"
            resp = safe_requests_get(dict_url, params={'greetingType': greeting_type}, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                if 'records' in data and len(data['records']) > 0:
                    
                    valid_records = [
                        r for r in data['records'] 
                        if r.get('type') == greeting_type 
                        and r.get('usageType') in [expected_usage, 'ExtensionAnsweringRule']
                    ]
                    
                    if not valid_records:
                        valid_records = [r for r in data['records'] if r.get('type') == greeting_type]

                    if valid_records:
                        target_names = ["Default", "Acoustic", "Ring tones", "Beautiful", "Corporate", "None"]
                        rec = next((r for r in valid_records if r.get('name') in target_names), valid_records[0])

                        content_uri = rec.get('contentUri')
                        if not content_uri and 'uri' in rec:
                            m_resp = safe_requests_get(rec['uri'], headers=headers)
                            if m_resp.status_code == 200:
                                content_uri = m_resp.json().get('contentUri')

                        if content_uri and 'mailboxId=' in content_uri:
                            content_uri = re.sub(r'mailboxId=\d+', f'mailboxId={ext_id}', content_uri)
            
    if not content_uri:
        if skip_fallback:
            raise Exception("No physical audio file explicitly mapped in RingCentral for export.")
            
        default_text = "This is the factory default system audio."
        if greeting_type == 'Voicemail':
            default_text = "Please leave your message after the tone."
        elif greeting_type in ('HoldMusic', 'ConnectingAudio'):
            default_text = "System Default Hold Music."
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
        
    response = safe_requests_get(content_uri, headers=headers)
    if response.status_code == 200:
        return response.content, response.headers.get('Content-Type', 'audio/mpeg')
        
    raise Exception(f"RC Media Fetch Failed. Status: {response.status_code}")


def upload_custom_greeting(ext_id, file_obj, greeting_type_str, greeting_name=None):
    """Uploads a custom greeting and BINDS it, navigating the V1/V2 CHaF migration gap safely."""
    ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
    ext_type = ext_info.get('type') if ext_info else None
    
    file_data = file_obj.read()
    filename = file_obj.filename
    content_type = getattr(file_obj, 'content_type', 'audio/wav')
    
    rule_id = None
    greeting_type = greeting_type_str
    if ":" in greeting_type_str:
        rule_id, greeting_type = greeting_type_str.split(":", 1)
    
    # 1. IVR MENU HANDLING (Unaffected by CHaF)
    if ext_type == 'IvrMenu':
        prompt_name = greeting_name or filename.split('.')[0]
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

    # 2. UPLOAD AUDIO RAW TO GET AN ID (Ensures audio is always added to the library)
    metadata = {"type": greeting_type}
    files_raw = {
        'json': ('request.json', json.dumps(metadata), 'application/json'),
        'attachment': (filename, file_data, content_type)
    }
    
    greeting_result = rc_api_call(
        f'/restapi/v1.0/account/~/extension/{ext_id}/greeting',
        method='POST',
        files=files_raw,
        raise_error=True
    )
    
    audio_id = greeting_result.get('id')
    if not audio_id:
        return greeting_result

    # 3. ATTEMPT LEGACY V1 BIND
    try:
        v1_payload = { "greetings": [ { "type": greeting_type, "custom": { "id": audio_id } } ] }
        rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}',
            method='PUT',
            json=v1_payload,
            raise_error=True
        )
        return greeting_result
        
    except Exception as e:
        # If the error is NOT related to CHaF blocking V1, throw it immediately.
        if 'CMN-468' not in str(e):
            raise e
            
    # 4. FALLBACK TO V2 CHaF BINDING
    # V1 was blocked by CMN-468. We will leverage RingCentral's V2 stateId shortcut via POST.
    state_id = 'work-hours' if rule_id == 'business-hours-rule' else 'after-hours'
    
    try:
        metadata_v2 = {"type": greeting_type, "stateId": state_id}
        files_v2 = {
            'json': ('request.json', json.dumps(metadata_v2), 'application/json'),
            'attachment': (filename, file_data, content_type)
        }
        return rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/greeting?apply=true',
            method='POST',
            files=files_v2,
            raise_error=True
        )
    except Exception as v2_err:
        # If V2 rejects the apply=true binding for HoldMusic, it means RC has no automated binding path yet.
        if greeting_type in ['HoldMusic', 'InterruptPrompt'] and 'CMN-101' in str(v2_err):
            raise Exception("Audio successfully uploaded to your Library, but RingCentral V2 lacks an API to auto-assign Hold Music. Please assign it manually in the portal.")
        raise Exception(f"Audio uploaded to Library, but V2 binding failed: {str(v2_err)}")


def generate_tts_audio_bytes(text, voice_name="Kore", style="professional and clear"):
    """Uses Gemini to generate TTS and returns an in-memory WAV buffer."""
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

def bulk_export_greetings(ext_ids, task_id=None, ignore_defaults=False):
    """Compiles a ZIP archive. Paces downloads safely and audits explicit errors into the CSV."""
    zip_buffer = io.BytesIO()
    csv_data = io.StringIO()
    csv_writer = csv.writer(csv_data)
    csv_writer.writerow(['Endpoint Name', 'Extension Number', 'Endpoint Type', 'Rule / Context', 'Greeting Type', 'Source', 'Exported Filename', 'Original Text / Details'])
    
    total = len(ext_ids)
    if task_id:
        export_progress_store[task_id] = {'current': 0, 'total': total}

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for i, ext_id in enumerate(ext_ids):
            ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
            if not ext_info: continue
            
            ext_name = ext_info.get('name', 'Unknown')
            ext_num = ext_info.get('extensionNumber', ext_id)
            ext_type = ext_info.get('type', 'Unknown')
            
            safe_ext_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', ext_name).strip()
            
            greetings = fetch_custom_greetings(ext_id)
            if greetings.get('status') == 'Success' and 'records' in greetings:
                for g in greetings['records']:
                    # FORCE SKIP IF "CUSTOM ONLY" TOGGLED
                    if ignore_defaults and not g['is_custom']:
                        continue
                    
                    # SKIP UNCONFIGURED SLOTS ALWAYS
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
                    
                    if is_custom:
                        source = "Custom Audio"
                    elif g_id == 'tts':
                        source = "TTS String"
                    else:
                        source = "System Preset"

                    if g_id not in ['tts']:
                        try:
                            # skip_fallback=True guarantees no AI generation fires during export
                            audio_bytes, mime = download_greeting_audio(
                                ext_id, g_id, is_ivr=is_ivr, is_custom=is_custom, 
                                greeting_type=g_type, preset_uri=g.get('preset_uri'),
                                skip_fallback=True
                            )
                            file_ext = 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'wav'
                            export_filename = f"[{ext_num}] {safe_ext_name} - {safe_rule} - {g_type}.{file_ext}"
                            zip_file.writestr(export_filename, audio_bytes)
                            
                            time.sleep(0.5)
                            
                        except Exception as e:
                            export_filename = f"ERROR: {str(e)}"
                    else:
                        export_filename = "N/A (Text-To-Speech String)"

                    csv_writer.writerow([ext_name, ext_num, ext_type, rule_name, g_type, source, export_filename, g_text])
            
            if task_id:
                export_progress_store[task_id]['current'] = i + 1
                    
        zip_file.writestr("Greeting_Mapping_Audit.csv", csv_data.getvalue())
        
    zip_buffer.seek(0)
    return zip_buffer
