import re
import copy
import time
import io
import json
import pandas as pd
import requests
from datetime import datetime
from webapp.rc_api import rc_api_call

audit_progress_store = {}

DAY_ABBR = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday", 
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"
}

_READ_ONLY = ('uri', 'id', 'type', 'name', 'creationTime', 'lastModifiedTime')

def to_int(val):
    try: return int(float(val))
    except (TypeError, ValueError): return None

def parse_time_to_seconds(val):
    if pd.isna(val) or val == '': return None
    val_str = str(val).lower().strip()
    match = re.search(r'([\d.]+)', val_str)
    if not match: return None
    num = float(match.group(1))
    return int(num * 60) if 'min' in val_str else int(num)

def format_sec(val):
    if val is None or str(val).strip() == "" or str(val).strip() == "None": return "None"
    try:
        v = int(float(val))
        if v == 0: return "0 Seconds"
        if v >= 60 and v % 60 == 0:
            mins = v // 60
            return f"{mins} Minute{'s' if mins > 1 else ''}"
        return f"{v} Seconds"
    except: return str(val)

def format_schedule(schedule_dict):
    if not schedule_dict: return "24/7"
    day_order = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 7}
    sorted_days = sorted(schedule_dict.items(), key=lambda x: day_order.get(x[0].lower(), 99))
    
    days = []
    for day, times in sorted_days:
        time_strs = []
        for t in times:
            if t['from'] == "00:00" and t['to'] == "23:59": time_strs.append("24/7")
            else: time_strs.append(f"{t['from']}-{t['to']}")
        days.append(f"{day[:3].capitalize()}: {', '.join(time_strs)}")
    return " | ".join(days)

def get_impersonation_token(employee_token, target_account_id):
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "access_token": employee_token}
    payload = {"accountId": str(target_account_id), "appName": "brd"}
    try:
        resp = requests.post(exchange_url, headers=headers, json=payload)
        if resp.ok: return resp.json().get("access_token")
    except: pass
    return None

def to_24h(time_str):
    time_str = time_str.replace(" ", "").lower()
    if "am" in time_str or "pm" in time_str:
        fmt = "%I:%M%p" if ":" in time_str else "%I%p"
        return datetime.strptime(time_str, fmt).strftime("%H:%M")
    return datetime.strptime(time_str, "%H:%M").strftime("%H:%M")

def parse_intuitive_hours(hours_str):
    hours_str = str(hours_str).lower().strip().replace("\n", " ").replace("–", "-").replace("—", "-")
    hours_str = re.sub(r'(?<!\d)(\d{1,2})\.(\d{2})\s*([ap]m)?', r'\1:\2\3', hours_str)
    hours_str = re.sub(r'(?<!\d)(\d{1,2}(?::\d{2})?)\s*(?:-|to|thru|through)\s*(\d{1,2}(?::\d{2})?\s*pm)', r'\1am-\2', hours_str)
    
    if hours_str in ['24/7', '24x7', '24-7', '24 7']: return "24/7"
    if not hours_str or hours_str in ['closed', 'none', 'n/a', 'off']: return {} 
        
    time_pattern = r'(?:\d{1,2}(?::\d{2})?\s*[ap]m|\d{1,2}:\d{2})\s*(?:-|to|thru|through)\s*(?:\d{1,2}(?::\d{2})?\s*[ap]m|\d{1,2}:\d{2})'
    parts = re.split(f'({time_pattern})', hours_str)
    if len(parts) == 1: raise ValueError(f"Could not detect valid time ranges in text: '{hours_str}'.")
        
    times = [parts[i] for i in range(1, len(parts), 2)]
    texts = [parts[i] for i in range(0, len(parts), 2)]
    
    day_map = {
        r'\bmonday\b': 'mon', r'\bm\b': 'mon', r'\btuesday\b': 'tue', r'\btues\b': 'tue', r'\btu\b': 'tue',
        r'\bwednesday\b': 'wed', r'\bw\b': 'wed', r'\bthursday\b': 'thu', r'\bthurs\b': 'thu', r'\bthu\b': 'thu', r'\bth\b': 'thu',
        r'\bfriday\b': 'fri', r'\bf\b': 'fri', r'\bsaturday\b': 'sat', r'\bsa\b': 'sat', r'\bsunday\b': 'sun', r'\bsu\b': 'sun'
    }
    
    days_before = False
    if texts and texts[0].strip():
        days_before = bool(re.search(r'\b(m|tu|tue|tues|w|wed|th|thu|thurs|f|fri|sa|sat|su|sun|mon|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', texts[0]))

    weekly_ranges = {}
    keys = list(DAY_ABBR.keys())
    
    for i, t in enumerate(times):
        assoc_text = texts[i] if days_before else texts[i+1]
        for pat, rep in day_map.items(): assoc_text = re.sub(pat, rep, assoc_text)
            
        time_match = re.search(r'(\d{1,2}(?::\d{2})?\s*[ap]m|\d{1,2}:\d{2})\s*(?:-|to|thru|through)\s*(\d{1,2}(?::\d{2})?\s*[ap]m|\d{1,2}:\d{2})', t)
        if not time_match: continue
        start_24 = to_24h(time_match.group(1))
        end_24 = to_24h(time_match.group(2))
        
        day_tokens = re.findall(r'(mon|tue|wed|thu|fri|sat|sun)', assoc_text)
        day_ranges = re.findall(r'(mon|tue|wed|thu|fri|sat|sun)\s*(?:-|to|thru|through)\s*(mon|tue|wed|thu|fri|sat|sun)', assoc_text)
        
        days_to_apply = set()
        if day_ranges:
            for d_start, d_end in day_ranges:
                idx_start, idx_end = keys.index(d_start), keys.index(d_end)
                if idx_start <= idx_end:
                    for j in range(idx_start, idx_end + 1): days_to_apply.add(keys[j])
                else: 
                    for j in range(idx_start, 7): days_to_apply.add(keys[j])
                    for j in range(0, idx_end + 1): days_to_apply.add(keys[j])
        elif day_tokens:
            for dt in day_tokens: days_to_apply.add(dt)
        else:
            if i == 0 and len(times) == 1: days_to_apply = set(keys)
            
        for d in days_to_apply:
            full_day = DAY_ABBR[d]
            if full_day not in weekly_ranges: weekly_ranges[full_day] = []
            weekly_ranges[full_day].append({"from": start_24, "to": end_24})
            
    if not weekly_ranges: raise ValueError("Could not detect any valid days associated with the provided times.")
    return weekly_ranges

def format_api_error(err_str):
    try:
        err_json = json.loads(err_str)
        if 'errors' in err_json:
            msgs = []
            for e in err_json['errors']:
                code = e.get('errorCode', 'Error')
                msg = e.get('message', '')
                param = e.get('parameterName', '')
                if param: msgs.append(f"{code}: {msg} [{param}]")
                else: msgs.append(f"{code}: {msg}")
            return " | ".join(msgs)
        return err_json.get('message', str(err_str))
    except:
        return str(err_str)

def safe_api_call(endpoint, method='GET', json_payload=None, token=None, max_retries=4):
    if 'mock_' in str(endpoint):
        if method == 'GET':
            if 'business-hours' in str(endpoint): return True, {"schedule": {"weeklyRanges": {}}}
            if 'answering-rule' in str(endpoint): return True, {"queue": {}}
            if 'voice-interaction-rules' in str(endpoint): return True, {"dispatching": {"actions": []}}
            if 'notification-settings' in str(endpoint): return True, {"voicemails": {}}
            if 'members' in str(endpoint): return True, {"records": []}
            if 'managers' in str(endpoint): return True, {"records": []}
            return True, {"name": "New Queue", "status": "NotActivated", "contact": {}}
        return True, {}

    for attempt in range(max_retries):
        try:
            resp = rc_api_call(endpoint, method=method, json=json_payload, token=token, return_response=True)
            status_code = getattr(resp, 'status_code', None)
            if status_code == 429:
                try: retry_after = int(resp.headers.get('Retry-After', 60))
                except: retry_after = 60
                time.sleep(retry_after + 1)
                continue
            if resp and getattr(resp, 'ok', False):
                try: return True, resp.json() if resp.content else {}
                except: return True, {}
            try: 
                err_msg = json.dumps(resp.json())
            except: 
                body_text = getattr(resp, 'text', '')
                err_msg = body_text if body_text else f'HTTP {status_code} Error (empty response body)'
            return False, err_msg
        except Exception as e: 
            time.sleep(2)
    return False, "Max retries exceeded due to rate limiting."

def fetch_directory(endpoint, token):
    records = []
    page = 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        succ, resp = safe_api_call(f'{endpoint}{sep}perPage=1000&page={page}', method='GET', token=token)
        if not succ:
            return False, resp
        if isinstance(resp, dict) and 'records' in resp:
            records.extend(resp['records'])
            if 'navigation' in resp and 'nextPage' in resp.get('navigation', {}): page += 1
            else: break
        else:
            break
    return True, records

def _safe_get_transfer_id(transfer_data, action_type):
    if not transfer_data: return ''
    if isinstance(transfer_data, list):
        for item in transfer_data:
            if item.get('action') == action_type:
                return str(item.get('extension', {}).get('id', ''))
    return ''

def _set_queue_transfer(q_set, action_type, ext_id):
    if 'transfer' not in q_set or not isinstance(q_set['transfer'], list):
        q_set['transfer'] = []
    
    q_set['transfer'] = [t for t in q_set['transfer'] if t.get('action') != action_type]
    
    if ext_id and ext_id != 'None':
        q_set['transfer'].append({
            "extension": {"id": ext_id},
            "action": action_type
        })
        
    if len(q_set['transfer']) == 0:
        q_set.pop('transfer', None)

def _safe_get_ah_transfer_id(transfer_data):
    if not transfer_data: return ''
    if isinstance(transfer_data, list) and len(transfer_data) > 0:
        return str(transfer_data[0].get('extension', {}).get('id', ''))
    return ''

def get_old_greeting_name(orig_rule, slot_type):
    """Safely looks up legacy greeting names to power diff-checking."""
    for g in orig_rule.get('greetings', []):
        if g.get('type') == slot_type:
            if 'preset' in g:
                name = g['preset'].get('name', 'Default')
                if name.lower() == 'none': return 'Off'
                return name
            elif 'custom' in g:
                return 'Custom'
    return 'Default'

def run_cq_audit(task_id, queue_ids, token):
    audit_progress_store[task_id] = {'current': 0, 'total': len(queue_ids), 'status': 'running', 'file_ready': False}
    try:
        ext_id_to_num = {}
        succ, ext_records = fetch_directory('/restapi/v1.0/account/~/extension', token)
        if succ:
            for e in ext_records:
                ext_id_to_num[str(e['id'])] = str(e.get('extensionNumber', ''))

        succ, sites_resp = safe_api_call('/restapi/v1.0/account/~/sites', token=token)
        site_map = {str(s['id']): s['name'] for s in sites_resp.get('records', [])} if succ else {}

        succ, tz_resp = fetch_directory('/restapi/v1.0/dictionary/timezone', token)
        tz_map = {str(t['id']): t['name'] for t in tz_resp} if succ else {}

        preset_dict = {'Introductory': {}, 'ConnectingMessage': {}, 'HoldMusic': {}, 'InterruptPrompt': {}, 'Voicemail': {}}
        for g_type in preset_dict.keys():
            succ, dict_resp_fallback = safe_api_call(f'/restapi/v1.0/dictionary/greeting?greetingType={g_type}&perPage=1000', method='GET', token=token)
            if succ and isinstance(dict_resp_fallback, dict) and 'records' in dict_resp_fallback:
                for rec in dict_resp_fallback['records']:
                    preset_dict[g_type][str(rec.get('id', ''))] = str(rec.get('name', '')).title()
                    
            succ, dict_resp = safe_api_call(f'/restapi/v1.0/dictionary/greeting?greetingType={g_type}&usageType=DepartmentExtensionAnsweringRule&perPage=1000', method='GET', token=token)
            if succ and isinstance(dict_resp, dict) and 'records' in dict_resp:
                for rec in dict_resp['records']:
                    preset_dict[g_type][str(rec.get('id', ''))] = str(rec.get('name', '')).title()

        rows = []
        for idx, qid in enumerate(queue_ids):
            audit_progress_store[task_id]['current'] = idx + 1
            row = {}
            
            succ, base = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}', token=token)
            if not succ: continue
            
            row["Queue Name"] = base.get('name', '')
            row["Extension"] = base.get('extensionNumber', '')
            row["Status"] = base.get('status', '').capitalize()
            row["Queue Email"] = base.get('contact', {}).get('email', '')
            
            editable = base.get('editableMemberStatus')
            if editable is True:
                row["Member Queue Status"] = "Allowed"
            elif editable is False:
                row["Member Queue Status"] = "Not Allowed"
            
            site_id = str(base.get('site', {}).get('id', ''))
            row["Site"] = site_map.get(site_id, site_id) if site_id else 'Main Site'
            
            tz_id = str(base.get('regionalSettings', {}).get('timezone', {}).get('id', ''))
            row["Timezone"] = tz_map.get(tz_id, tz_id)

            succ, bh = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}/business-hours', token=token)
            if succ and bh.get('schedule', {}).get('weeklyRanges'):
                row["Hours"] = format_schedule(bh['schedule']['weeklyRanges'])
            else:
                row["Hours"] = "24/7"

            succ, rule = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}/answering-rule/business-hours-rule', token=token)
            if succ:
                q_set = rule.get('queue', {})
                
                transfer_mode = q_set.get('transferMode', 'Simultaneous')
                if transfer_mode == 'Rotating': row["Ring Type"] = "Rotating"
                else: row["Ring Type"] = transfer_mode
                
                row["User Ring Time"] = format_sec(q_set.get('agentTimeout'))
                row["Total Ring Time"] = format_sec(q_set.get('holdTime'))
                row["Wrap Up Time"] = format_sec(q_set.get('wrapUpTime'))
                row["Callers In Queue"] = q_set.get('maxCallers')
                
                row["When Queue is Full"] = q_set.get('maxCallersAction')
                f_ext = _safe_get_transfer_id(q_set.get('transfer'), 'MaxCallers')
                if f_ext: row["Queue Full Destination"] = ext_id_to_num.get(f_ext, f_ext)

                row["When Max Time is Reached"] = q_set.get('holdTimeExpirationAction')
                t_ext = _safe_get_transfer_id(q_set.get('transfer'), 'HoldTimeExpiration')
                if t_ext: row["Time Reached Destination"] = ext_id_to_num.get(t_ext, t_ext)

                mode = q_set.get('holdAudioInterruptionMode')
                if mode == 'Never' or not mode:
                    row["Interrupt Audio"] = "Never"
                else:
                    row["Interrupt Audio"] = format_sec(q_set.get('holdAudioInterruptionPeriod'))

                vm_recip = str(rule.get('voicemail', {}).get('recipient', {}).get('id', ''))
                if vm_recip and vm_recip != 'None':
                    row["Voicemail Recipients"] = ext_id_to_num.get(vm_recip, vm_recip)

                for g in rule.get('greetings', []):
                    if g.get('type') == 'Voicemail':
                        if 'custom' in g and g.get('custom', {}).get('name'): row["Voicemail Greeting"] = 'Custom'
                        elif 'preset' in g and g.get('preset', {}).get('name'): row["Voicemail Greeting"] = g['preset']['name']
                        else: row["Voicemail Greeting"] = 'Default'

            # Audit VIR for Greetings
            succ, vir = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}/voice-interaction-rules/business-hours-rule', token=token)
            if succ:
                actions = vir.get('dispatching', {}).get('actions', [])
                for act in actions:
                    a_type = act.get('type')
                    is_on = act.get('enabled', True)
                    
                    if not is_on:
                        g_name = "Off"
                    else:
                        g_id = act.get('greeting', {}).get('preset', {}).get('id')
                        if not g_id:
                            g_name = "Custom" if act.get('greeting', {}).get('custom') else "Default"
                        else:
                            dict_type = 'Introductory' if a_type == 'PlayWelcomePromptAction' else \
                                        'ConnectingMessage' if a_type == 'PlayConnectingMessageAction' else \
                                        'HoldMusic' if a_type == 'PlayHoldMusicAction' else \
                                        'InterruptPrompt' if a_type == 'PlayInterruptPromptAction' else 'Introductory'
                            
                            g_name = preset_dict.get(dict_type, {}).get(str(g_id), "Default")
                    
                    if a_type == 'PlayWelcomePromptAction': row["Greeting"] = g_name.title() if g_name != 'Off' else g_name
                    elif a_type == 'PlayConnectingMessageAction': row["Audio While Connecting"] = g_name.title() if g_name != 'Off' else g_name
                    elif a_type == 'PlayHoldMusicAction': row["Hold Music"] = g_name.title() if g_name != 'Off' else g_name
                    elif a_type == 'PlayInterruptPromptAction': 
                        if 'patience' in g_name.lower(): row["Interrupt Prompt"] = "Thank you for your patience"
                        elif 'volume' in g_name.lower(): row["Interrupt Prompt"] = "Higher than normal volume"
                        elif 'busy' in g_name.lower(): row["Interrupt Prompt"] = "Agents are currently busy"
                        elif 'important' in g_name.lower(): row["Interrupt Prompt"] = "Call is very important to us"
                        else: row["Interrupt Prompt"] = g_name.title() if g_name != 'Off' else g_name

            succ, ah_rule = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}/answering-rule/after-hours-rule', token=token)
            if succ:
                row["After Hours Behavior"] = ah_rule.get('callHandlingAction')
                a_ext = _safe_get_ah_transfer_id(ah_rule.get('transfer'))
                if a_ext and a_ext != 'None':
                    row["After Hours Destination"] = ext_id_to_num.get(a_ext, a_ext)
                
                if not row.get("Voicemail Recipients"):
                    vm_recip_ah = str(ah_rule.get('voicemail', {}).get('recipient', {}).get('id', ''))
                    if vm_recip_ah and vm_recip_ah != 'None':
                        row["Voicemail Recipients"] = ext_id_to_num.get(vm_recip_ah, vm_recip_ah)

            succ, mgr_resp = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{qid}/managers', token=token)
            if succ and mgr_resp.get('records'):
                mgrs = [ext_id_to_num.get(str(m.get('id', '')), '') for m in mgr_resp['records']]
                mgrs = [m for m in mgrs if m]
                if mgrs:
                    row["Queue Manager"] = ", ".join(mgrs)

            succ, mem_resp = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{qid}/members', token=token)
            if succ and mem_resp.get('records'):
                mems = [str(m.get('extensionNumber')) for m in mem_resp['records'] if m.get('extensionNumber')]
                row["Members (Ext)"] = ", ".join(mems)

            succ, notif = safe_api_call(f'/restapi/v1.0/account/~/extension/{qid}/notification-settings', token=token)
            if succ:
                vm_set = notif.get('voicemails', {})
                if vm_set.get('notifyByEmail'):
                    if vm_set.get('markAsRead'): row["Voicemail Notifications"] = "Notify Attach & Read"
                    elif vm_set.get('includeAttachment'): row["Voicemail Notifications"] = "Notify & Attach"
                    else: row["Voicemail Notifications"] = "Notify by Email"
                else:
                    row["Voicemail Notifications"] = "Off"
                
                if notif.get('advancedMode'):
                    emails = vm_set.get('emailAddresses', [])
                else:
                    emails = notif.get('emailAddresses', [])
                    
                if emails: row["Voicemail Notifications Email"] = ", ".join(emails)

            rows.append(row)
            time.sleep(0.35) 

        df = pd.DataFrame(rows)
        template_cols = [
            "Queue Name", "Record Group Name", "Extension", "Site", "Status", "Phone Number", 
            "Queue Manager", "Queue Email", "Queue PIN", "Members (Ext)", "Timezone", "Hours", 
            "Greeting", "Audio While Connecting", "Hold Music", "Interrupt Audio", "Interrupt Prompt", 
            "Ring Type", "User Ring Time", "Total Ring Time", "Wrap Up Time", "Member Queue Status", 
            "Callers In Queue", "When Queue is Full", "Queue Full Destination", "When Max Time is Reached", 
            "Time Reached Destination", "Voicemail Greeting", "Voicemail Recipients", 
            "Voicemail Notifications", "Voicemail Notifications Email", "After Hours Behavior", 
            "After Hours Destination"
        ]
        
        for col in template_cols:
            if col not in df.columns:
                df[col] = ""
        df = df[template_cols]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Queue Config')
            
            global_timezones = [
                "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii",
                "Canada/Eastern", "Canada/Central", "Canada/Mountain", "Canada/Pacific", "Canada/Atlantic",
                "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Athens", "Europe/Moscow",
                "GMT", "UTC", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Asia/Hong_Kong",
                "Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth",
                "Pacific/Auckland", "America/Sao_Paulo", "America/Buenos_Aires", "America/Mexico_City"
            ]
            tz_df = pd.DataFrame({"ValidTimezones": global_timezones})
            tz_df.to_excel(writer, index=False, sheet_name='Timezone_Ref')
            
            workbook = writer.book
            config_ws = workbook['Queue Config']
            
            from openpyxl.worksheet.datavalidation import DataValidation
            dv_tz = DataValidation(type="list", formula1="=Timezone_Ref!$A$2:$A$" + str(len(global_timezones) + 1), allow_blank=True)
            config_ws.add_data_validation(dv_tz)
            dv_tz.add("K2:K1000") 
            
            schema_validations = {
                "E": '"Enabled,Disabled"', "M": '"Default,Custom,Off"',
                "N": '"Default,Ring tones,Acoustic,Beautiful,Corporate,Custom,Off"',
                "O": '"Default,Ring tones,Acoustic,Beautiful,Corporate,Custom,Off"',
                "P": '"Never,10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute"',
                "Q": '"Thank you for your patience,Higher than normal volume,Agents are currently busy,Call is very important to us,Custom,Default,Off"',
                "R": '"Simultaneous,Sequential,Rotating"', 
                "S": '"10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute,2 Minutes"',
                "T": '"15 Seconds,30 Seconds,45 Seconds,1 Minute,2 Minutes,3 Minutes,4 Minutes,5 Minutes,10 Minutes,15 Minutes"',
                "U": '"0 Seconds,5 Seconds,10 Seconds,15 Seconds,20 Seconds,30 Seconds,1 Minute"', 
                "V": '"Allowed,Not Allowed"',
                "W": '"5,10,15,20,25"', 
                "X": '"Voicemail,TransferToExtension,Disconnect,Announcement"',
                "Z": '"Voicemail,TransferToExtension,Disconnect,Announcement"', 
                "AB": '"Default,Custom,Off"',
                "AD": '"Off,Notify by Email,Notify & Attach,Notify Attach & Read"',
                "AF": '"TakeMessagesOnly,TransferToExtension,UnconditionalForwarding,PlayAnnouncementOnly,Disconnect"'
            }

            for col_letter, formula_string in schema_validations.items():
                dv = DataValidation(type="list", formula1=formula_string, allow_blank=True)
                config_ws.add_data_validation(dv)
                dv.add(f"{col_letter}2:{col_letter}1000")
                
            for col in config_ws.columns:
                for cell in col:
                    cell.number_format = '@'

        output.seek(0)
        audit_progress_store[task_id]['file_data'] = output.getvalue()
        audit_progress_store[task_id]['status'] = 'completed'
        audit_progress_store[task_id]['file_ready'] = True

    except Exception as e:
        audit_progress_store[task_id]['status'] = 'error'
        audit_progress_store[task_id]['error'] = str(e)

def get_val(row, key):
    clean_key = str(key).strip().lower()
    for k, v in row.items():
        if str(k).strip().lower() == clean_key:
            if pd.notna(v) and str(v).strip().lower() != 'nan': return str(v).strip()
    return None

def check_diff(changes_list, param_name, old_val, new_val):
    old_str = str(old_val).strip() if old_val is not None and str(old_val).strip() != '' else "None"
    new_str = str(new_val).strip() if new_val is not None and str(new_val).strip() != '' else "None"
    if old_str != new_str:
        changes_list.append({"parameter": param_name, "old": old_str, "new": new_str})
        return True
    return False

def update_cq_batch(records, token, is_preview=False):
    total_records = len(records)
    yield {"type": "start", "total": total_records, "message": "Fetching Account Directories..."}
    
    succ, test_resp = safe_api_call('/restapi/v1.0/account/~', token=token)
    if not succ:
        yield {"type": "error", "message": f"Unauthorized. Token expired or invalid. Details: {format_api_error(test_resp)}"}
        return

    queue_map, ext_map, site_map, site_id_to_name = {}, {}, {}, {}
    tz_map, tz_id_to_name = {}, {}
    ext_id_to_num = {}
    
    succ, q_records = fetch_directory('/restapi/v1.0/account/~/call-queues', token)
    if succ:
        for q in q_records:
            if 'extensionNumber' in q: 
                queue_map[str(q['extensionNumber'])] = str(q['id'])
                ext_id_to_num[str(q['id'])] = str(q['extensionNumber'])
    else:
        yield {"type": "error", "message": "Failed to load queues directory."}
        return

    succ, e_records = fetch_directory('/restapi/v1.0/account/~/extension', token)
    if succ:
        for e in e_records:
            if 'extensionNumber' in e: 
                ext_map[str(e['extensionNumber'])] = str(e['id'])
                ext_id_to_num[str(e['id'])] = str(e['extensionNumber'])
    else:
        yield {"type": "error", "message": "Failed to load extensions directory."}
        return

    succ, s_records = fetch_directory('/restapi/v1.0/account/~/sites', token)
    if succ:
        for s in s_records:
            s_id = str(s['id'])
            s_name_dict = str(s.get('name')).lower().strip()
            site_map[s_name_dict] = s_id
            site_id_to_name[s_id] = str(s.get('name'))
            if s.get('code') == 'main-site' or s_name_dict == 'main site':
                site_map['main site'] = s_id
                site_map['company'] = s_id

    succ, tz_records = fetch_directory('/restapi/v1.0/dictionary/timezone', token)
    if succ:
        for tz in tz_records:
            tz_map[str(tz.get('name')).lower().strip()] = str(tz['id'])
            tz_map[str(tz.get('id'))] = str(tz['id'])
            tz_id_to_name[str(tz['id'])] = str(tz.get('name'))
    else:
        yield {"type": "error", "message": "Failed to load timezone dictionary."}
        return

    preset_dict = {'Introductory': {}, 'ConnectingMessage': {}, 'HoldMusic': {}, 'InterruptPrompt': {}, 'Voicemail': {}}
    for g_type in preset_dict.keys():
        succ, dict_resp_fallback = safe_api_call(f'/restapi/v1.0/dictionary/greeting?greetingType={g_type}&perPage=1000', method='GET', token=token)
        if succ and isinstance(dict_resp_fallback, dict) and 'records' in dict_resp_fallback:
            for rec in dict_resp_fallback['records']:
                k = rec.get('name', '').lower().strip()
                preset_dict[g_type][k] = str(rec.get('id', ''))
                
        succ, dict_resp = safe_api_call(f'/restapi/v1.0/dictionary/greeting?greetingType={g_type}&usageType=DepartmentExtensionAnsweringRule&perPage=1000', method='GET', token=token)
        if succ and isinstance(dict_resp, dict) and 'records' in dict_resp:
            for rec in dict_resp['records']:
                preset_dict[g_type][rec.get('name', '').lower().strip()] = str(rec.get('id', ''))

    def _resolve_ext(num):
        clean_num = str(num).split('.')[0].strip()
        if clean_num in ext_map: return ext_map[clean_num]
        return clean_num

    for i, row in enumerate(records):
        logs = []
        changes = []
        has_error = False
        
        ext_raw = get_val(row, 'Extension') or get_val(row, 'Extension Number')
        if not ext_raw: 
            yield {"type": "progress", "current": i + 1, "total": total_records, "result": {"ext": "N/A", "status": "info", "message": "Skipped row", "changes": []}, "is_preview": is_preview}
            continue
            
        ext_num = str(ext_raw).split('.')[0].strip()
        q_id = queue_map.get(ext_num)
        
        if not q_id:
            succ, resp = safe_api_call(f'/restapi/v1.0/account/~/extension?extensionNumber={ext_num}', method='GET', token=token)
            if succ and isinstance(resp, dict) and resp.get('records'):
                for rec in resp['records']:
                    if rec.get('type') == 'Department':
                        q_id = str(rec['id'])
                        queue_map[ext_num] = q_id
                        break
                        
        if not q_id:
            q_name = get_val(row, 'Queue Name')
            if not q_name:
                yield {"type": "progress", "current": i + 1, "total": total_records, "result": {"ext": ext_num, "status": "error", "message": "Call Queue not found. 'Queue Name' is required to create a new one.", "changes": []}, "is_preview": is_preview}
                continue
                
            if is_preview:
                q_id = f"mock_{ext_num}"
                queue_map[ext_num] = q_id
                ext_id_to_num[q_id] = ext_num
                changes.append({"parameter": "Queue", "old": "Missing", "new": "Will be created"})
                logs.append("Queue will be created")
            else:
                create_payload = {
                    "type": "Department",
                    "extensionNumber": ext_num,
                    "contact": { "firstName": q_name }
                }
                c_status = get_val(row, 'Status')
                if c_status: create_payload['status'] = c_status.capitalize()
                
                c_email = get_val(row, 'Queue Email')
                if c_email: create_payload['contact']['email'] = c_email
                    
                succ, c_resp = safe_api_call('/restapi/v1.0/account/~/extension', method='POST', json_payload=create_payload, token=token)
                if succ and isinstance(c_resp, dict) and c_resp.get('id'):
                    q_id = str(c_resp['id'])
                    queue_map[ext_num] = q_id
                    ext_map[ext_num] = q_id
                    ext_id_to_num[q_id] = ext_num
                    changes.append({"parameter": "Queue", "old": "Missing", "new": "Created"})
                    logs.append("Queue Created")
                    time.sleep(1.0)
                else:
                    yield {"type": "progress", "current": i + 1, "total": total_records, "result": {"ext": ext_num, "status": "error", "message": f"Failed to create Queue: {format_api_error(c_resp)}", "changes": changes}, "is_preview": is_preview}
                    continue

        # --- PRE-FETCH LEGACY ANSWERING RULE FOR DIFFING ---
        routing_fields = [
            'Ring Type', 'User Ring Time', 'Total Ring Time', 'Wrap Up Time', 
            'When Max Time is Reached', 'When Queue is Full', 'Callers In Queue', 
            'Interrupt Audio', 'Time Reached Destination', 'Queue Full Destination', 'Voicemail Recipients', 'Voicemail Greeting'
        ]
        vir_fields = ['Greeting', 'Audio While Connecting', 'Hold Music', 'Interrupt Prompt']
        all_routing_fields = routing_fields + vir_fields
        
        orig_rule = {}
        if any(get_val(row, f) is not None for f in all_routing_fields):
            get_succ, rule = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/business-hours-rule', method='GET', token=token)
            if get_succ and isinstance(rule, dict):
                orig_rule = copy.deepcopy(rule)

        # --- A. BASIC INFO UPDATE ---
        basic_fields = ['Queue Name', 'Status', 'Queue Email', 'Site', 'Timezone', 'Time Zone', 'Member Queue Status']
        if any(get_val(row, f) is not None for f in basic_fields):
            get_succ, old_basic = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}', method='GET', token=token)
            if get_succ and isinstance(old_basic, dict):
                basic_payload = {}
                b_needs_update = False
                
                val_qn = get_val(row, 'Queue Name')
                if val_qn is not None: 
                    basic_payload['name'] = val_qn
                    b_needs_update |= check_diff(changes, 'Queue Name', old_basic.get('name'), basic_payload['name'])
                    
                val_st = get_val(row, 'Status')
                if val_st is not None: 
                    basic_payload['status'] = val_st.capitalize()
                    b_needs_update |= check_diff(changes, 'Status', old_basic.get('status'), basic_payload['status'])
                    
                val_qe = get_val(row, 'Queue Email')
                if val_qe is not None: 
                    basic_payload['contact'] = old_basic.get('contact', {})
                    basic_payload['contact']['email'] = val_qe
                    b_needs_update |= check_diff(changes, 'Queue Email', old_basic.get('contact', {}).get('email'), basic_payload['contact']['email'])

                val_site = get_val(row, 'Site')
                if val_site is not None:
                    s_name = val_site.lower()
                    if not site_map:
                        pass
                    else:
                        new_site_id = site_map.get(s_name)
                        if new_site_id: 
                            old_site_obj = old_basic.get('site', {})
                            old_site_id = str(old_site_obj.get('id', '')) if old_site_obj else ''
                            
                            if not old_site_id or old_site_id == 'None':
                                old_site_id = site_map.get('main site', 'main-site')

                            old_site_name = 'Main Site' if old_site_id in ('main-site', site_map.get('main site')) else site_id_to_name.get(old_site_id, old_site_id)
                            new_site_name = 'Main Site' if new_site_id in ('main-site', site_map.get('main site')) else site_id_to_name.get(new_site_id, new_site_id)
                            
                            if check_diff(changes, 'Site', old_site_name, new_site_name):
                                if new_site_id == 'main-site' or new_site_id == site_map.get('main site'):
                                    pass 
                                else:
                                    basic_payload['site'] = {'id': new_site_id}
                                    b_needs_update = True
                        else:
                            has_error = True
                            logs.append(f"Invalid Site: '{val_site}'")
                        
                tz_raw = get_val(row, 'Timezone') or get_val(row, 'Time Zone')
                if tz_raw is not None:
                    tz_key = tz_raw.lower()
                    if tz_key in tz_map:
                        if 'regionalSettings' not in basic_payload: basic_payload['regionalSettings'] = {}
                        new_tz_id = tz_map[tz_key]
                        basic_payload['regionalSettings']['timezone'] = {'id': new_tz_id}
                        
                        old_tz_id = str(old_basic.get('regionalSettings', {}).get('timezone', {}).get('id', ''))
                        old_tz_name = tz_id_to_name.get(old_tz_id, old_tz_id) if old_tz_id else "None"
                        new_tz_name = tz_id_to_name.get(new_tz_id, new_tz_id)
                        
                        b_needs_update |= check_diff(changes, 'Timezone', old_tz_name, new_tz_name)
                    else:
                        has_error = True; logs.append(f"Invalid Timezone: '{tz_raw}'")

                if b_needs_update:
                    if not is_preview:
                        put_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}', method='PUT', json_payload=basic_payload, token=token)
                        attempt = 0
                        while not put_succ and 'extensionId' in str(err) and attempt < 3:
                            time.sleep(2.0)
                            attempt += 1
                            put_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}', method='PUT', json_payload=basic_payload, token=token)
                        
                        if put_succ:
                            logs.append("Basic Info Updated")
                        else:
                            has_error = True
                            logs.append(f"Basic Error: {format_api_error(err)}")
                    else:
                        logs.append("Basic Info Evaluated")
                        
        val_mqs = get_val(row, 'Member Queue Status')
        if val_mqs is not None:
            get_succ, old_cq = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{q_id}', method='GET', token=token)
            if get_succ and isinstance(old_cq, dict):
                mem_status = val_mqs.lower()
                new_editable = True if 'allowed' in mem_status and 'not' not in mem_status else False
                
                old_editable = old_cq.get('editableMemberStatus')
                old_status_str = 'Allowed' if old_editable else 'Not Allowed'
                new_status_str = 'Allowed' if new_editable else 'Not Allowed'
                
                if check_diff(changes, 'Member Queue Status', old_status_str, new_status_str):
                    if not is_preview:
                        cq_payload = {"editableMemberStatus": new_editable}
                        s_succ, err = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{q_id}', method='PUT', json_payload=cq_payload, token=token)
                        if s_succ: 
                            logs.append("Member Queue Status Updated")
                        else: 
                            has_error = True
                            logs.append(f"Member Status Error: {format_api_error(err)}")
                    else:
                        logs.append("Member Queue Status Evaluated")

        # --- B. BUSINESS HOURS UPDATE ---
        hours_str = get_val(row, 'Hours')
        if hours_str is not None:
            try:
                weekly_ranges = parse_intuitive_hours(hours_str)
                old_hours_str = "Unknown"
                get_succ, old_hours_resp = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/business-hours', method='GET', token=token)
                
                if get_succ and isinstance(old_hours_resp, dict):
                    old_ranges = old_hours_resp.get('schedule', {}).get('weeklyRanges', {})
                    if not old_ranges and 'schedule' in old_hours_resp: old_hours_str = "24/7"
                    else: old_hours_str = format_schedule(old_ranges)

                new_hours_str = "24/7" if weekly_ranges == "24/7" else format_schedule(weekly_ranges)
                
                if old_hours_str != new_hours_str:
                    changes.append({"parameter": "Business Hours", "old": old_hours_str, "new": new_hours_str})
                    if not is_preview:
                        payload = {"schedule": {}} if weekly_ranges == "24/7" else {"schedule": {"weeklyRanges": weekly_ranges}}
                        s_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/business-hours', method='PUT', json_payload=payload, token=token)
                        if s_succ: 
                            logs.append("Hours Updated")
                        else: 
                            has_error = True
                            logs.append(f"Hours Error: {format_api_error(err)}")
                    else:
                        logs.append("Hours Evaluated")
            except Exception as e:
                has_error = True; logs.append(f"Hours Parse Error: {str(e)}")

        # --- C. ROUTING & TIMERS (Answering Rule) ---
        if any(get_val(row, f) is not None for f in routing_fields) and orig_rule:
            rule = copy.deepcopy(orig_rule)
            q_set = rule.get('queue', {})
            r_needs_update = False
            
            rt = get_val(row, 'Ring Type')
            if rt is not None:
                rt_lower = rt.lower()
                if 'simultaneous' in rt_lower: q_set['transferMode'] = 'Simultaneous'
                elif 'sequential' in rt_lower: q_set['transferMode'] = 'Sequential'
                elif 'rotating' in rt_lower or 'idle' in rt_lower: q_set['transferMode'] = 'Rotating'
            
            val_urt = get_val(row, 'User Ring Time')
            if val_urt is not None: 
                parsed = parse_time_to_seconds(val_urt)
                if parsed is not None: q_set['agentTimeout'] = parsed
                
            val_trt = get_val(row, 'Total Ring Time')
            if val_trt is not None:
                parsed = parse_time_to_seconds(val_trt)
                if parsed is not None: q_set['holdTime'] = parsed
                
            val_wut = get_val(row, 'Wrap Up Time')
            if val_wut is not None:
                parsed = parse_time_to_seconds(val_wut)
                if parsed is not None: q_set['wrapUpTime'] = parsed
                
            val_ciq = get_val(row, 'Callers In Queue')
            if val_ciq is not None:
                parsed = to_int(val_ciq)
                if parsed is not None: q_set['maxCallers'] = parsed
                
            val_ia = get_val(row, 'Interrupt Audio')
            if val_ia is not None:
                if val_ia.lower() == 'never':
                    q_set['holdAudioInterruptionMode'] = 'Never'
                    q_set.pop('holdAudioInterruptionPeriod', None)
                else:
                    parsed = parse_time_to_seconds(val_ia)
                    if parsed is not None: 
                        q_set['holdAudioInterruptionMode'] = 'Periodically'
                        q_set['holdAudioInterruptionPeriod'] = parsed

            val_wmtr = get_val(row, 'When Max Time is Reached')
            if val_wmtr is not None:
                if val_wmtr == 'TransferToExtension':
                    dest_id = _resolve_ext(get_val(row, 'Time Reached Destination'))
                    q_set['holdTimeExpirationAction'] = val_wmtr
                    _set_queue_transfer(q_set, 'HoldTimeExpiration', dest_id)
                else:
                    q_set['holdTimeExpirationAction'] = val_wmtr
                    _set_queue_transfer(q_set, 'HoldTimeExpiration', None)

            val_wqf = get_val(row, 'When Queue is Full')
            if val_wqf is not None:
                if val_wqf == 'TransferToExtension':
                    dest_id = _resolve_ext(get_val(row, 'Queue Full Destination'))
                    q_set['maxCallersAction'] = val_wqf
                    _set_queue_transfer(q_set, 'MaxCallers', dest_id)
                else:
                    q_set['maxCallersAction'] = val_wqf
                    _set_queue_transfer(q_set, 'MaxCallers', None)
                    
            if q_set.get('transferMode') == 'Simultaneous':
                q_set.pop('agentTimeout', None)

            rule['queue'] = q_set
            old_q = orig_rule.get('queue', {})
            
            old_tm = 'Rotating' if old_q.get('transferMode') == 'Rotating' else old_q.get('transferMode')
            new_tm = 'Rotating' if q_set.get('transferMode') == 'Rotating' else q_set.get('transferMode')
            
            if rt is not None:
                r_needs_update |= check_diff(changes, 'Ring Type', old_tm, new_tm)
            
            if val_urt is not None:
                r_needs_update |= check_diff(changes, 'User Ring Time', format_sec(old_q.get('agentTimeout')), format_sec(q_set.get('agentTimeout')))
            
            if val_trt is not None:
                r_needs_update |= check_diff(changes, 'Total Ring Time', format_sec(old_q.get('holdTime')), format_sec(q_set.get('holdTime')))
            
            if val_wut is not None:
                r_needs_update |= check_diff(changes, 'Wrap Up Time', format_sec(old_q.get('wrapUpTime')), format_sec(q_set.get('wrapUpTime')))
            
            if val_ciq is not None:
                r_needs_update |= check_diff(changes, 'Max Callers', old_q.get('maxCallers'), q_set.get('maxCallers'))
            
            if val_wqf is not None:
                r_needs_update |= check_diff(changes, 'Max Callers Action', old_q.get('maxCallersAction'), q_set.get('maxCallersAction'))
            
            old_f_id = _safe_get_transfer_id(old_q.get('transfer'), 'MaxCallers') or 'None'
            new_f_id = _safe_get_transfer_id(q_set.get('transfer'), 'MaxCallers') or 'None'
            if get_val(row, 'Queue Full Destination') is not None:
                r_needs_update |= check_diff(changes, 'Queue Full Dest', ext_id_to_num.get(old_f_id, old_f_id), ext_id_to_num.get(new_f_id, new_f_id))

            if val_wmtr is not None:
                r_needs_update |= check_diff(changes, 'Max Time Action', old_q.get('holdTimeExpirationAction'), q_set.get('holdTimeExpirationAction'))
            
            old_t_id = _safe_get_transfer_id(old_q.get('transfer'), 'HoldTimeExpiration') or 'None'
            new_t_id = _safe_get_transfer_id(q_set.get('transfer'), 'HoldTimeExpiration') or 'None'
            if get_val(row, 'Time Reached Destination') is not None:
                r_needs_update |= check_diff(changes, 'Max Time Dest', ext_id_to_num.get(old_t_id, old_t_id), ext_id_to_num.get(new_t_id, new_t_id))

            old_ia_mode = old_q.get('holdAudioInterruptionMode')
            if old_ia_mode == 'Never' or not old_ia_mode:
                old_ia_str = "Never"
            else:
                old_ia_str = format_sec(old_q.get('holdAudioInterruptionPeriod'))
                
            new_ia_mode = q_set.get('holdAudioInterruptionMode')
            if new_ia_mode == 'Never' or not new_ia_mode:
                new_ia_str = "Never"
            else:
                new_ia_str = format_sec(q_set.get('holdAudioInterruptionPeriod'))
                
            if val_ia is not None:
                r_needs_update |= check_diff(changes, 'Interrupt Audio', old_ia_str, new_ia_str)

            if 'greetings' in rule:
                rule['greetings'] = [g for g in rule['greetings'] if g.get('type') not in ['Introductory', 'ConnectingAudio', 'HoldMusic', 'InterruptPrompt']]

            vm_greet_val = get_val(row, 'Voicemail Greeting')
            if vm_greet_val is not None:
                vm_new_val = vm_greet_val.lower().strip()
                matched_id = preset_dict.get('Voicemail', {}).get(vm_new_val)
                
                # Safely clear out the old Voicemail greeting before appending the new one to prevent AWR-106 duplicates
                if 'greetings' in rule:
                    rule['greetings'] = [g for g in rule['greetings'] if g.get('type') != 'Voicemail']

                if vm_new_val in ['off', 'none', 'disable', 'disabled']:
                    pass 
                elif vm_new_val == 'default':
                    rule['greetings'].append({"type": "Voicemail", "preset": {"id": "Default"}})
                elif matched_id:
                    rule['greetings'].append({"type": "Voicemail", "preset": {"id": str(matched_id)}})
                else:
                    orig_g = next((g for g in orig_rule.get('greetings', []) if g.get('type') == 'Voicemail'), None)
                    if orig_g: rule['greetings'].append(orig_g)
                    
                old_val_name = get_old_greeting_name(orig_rule, 'Voicemail')
                r_needs_update |= check_diff(changes, 'Voicemail Greeting', old_val_name, vm_greet_val)
            
            vm_recip_raw = get_val(row, 'Voicemail Recipients')
            if vm_recip_raw is not None:
                vm_ext_id = _resolve_ext(vm_recip_raw)
                if vm_ext_id:
                    if 'voicemail' not in rule: rule['voicemail'] = {}
                    rule['voicemail']['recipient'] = {'id': vm_ext_id}
                    old_vm = str(orig_rule.get('voicemail', {}).get('recipient', {}).get('id', 'None'))
                    r_needs_update |= check_diff(changes, 'VM Recipient', ext_id_to_num.get(old_vm, old_vm), ext_id_to_num.get(vm_ext_id, vm_ext_id))

            for field in _READ_ONLY: rule.pop(field, None)
            for field in _READ_ONLY: 
                if 'queue' in rule: rule['queue'].pop(field, None)
            rule.pop('callers', None); rule.pop('calledNumbers', None)
            
            if r_needs_update and not is_preview:
                put_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/business-hours-rule', method='PUT', json_payload=rule, token=token)
                
                if not put_succ and ('transfer' in str(err) or 'transfer.extension.id' in str(err)):
                    rule['queue'].pop('transfer', None)
                    if rule['queue'].get('maxCallersAction') == 'TransferToExtension': rule['queue']['maxCallersAction'] = 'Voicemail'
                    if rule['queue'].get('holdTimeExpirationAction') == 'TransferToExtension': rule['queue']['holdTimeExpirationAction'] = 'Voicemail'
                    
                    put_succ2, err2 = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/business-hours-rule', method='PUT', json_payload=rule, token=token)
                    if put_succ2: logs.append("Routing Updated (Invalid transfers stripped & reverted to Voicemail)")
                    else: has_error = True; logs.append(f"Routing Error: {format_api_error(err2)}")
                
                elif put_succ: 
                    logs.append("Routing Updated")
                else: 
                    has_error = True
                    logs.append(f"Routing Error: {format_api_error(err)}")

        # --- D. VOICE INTERACTION RULES (Greetings) ---
        if any(get_val(row, f) is not None for f in vir_fields):
            vir_succ, vir_rule = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/voice-interaction-rules/business-hours-rule', method='GET', token=token)
            
            actions = []
            if vir_succ and isinstance(vir_rule, dict):
                actions = vir_rule.get('dispatching', {}).get('actions', [])
            
            vir_needs_update = False
            
            def apply_vir_greeting(col_name, act_type, dict_type, legacy_type):
                nonlocal vir_needs_update
                val = get_val(row, col_name)
                if not val: return
                
                new_val = val.lower().strip()
                
                old_val_str = "Unknown"
                existing_act = next((a for a in actions if a.get('type') == act_type), None)
                if existing_act:
                    if existing_act.get('enabled') is False:
                        old_val_str = "Off"
                    else:
                        preset_id = existing_act.get('greeting', {}).get('preset', {}).get('id')
                        if preset_id:
                            old_val_str = preset_dict.get(dict_type, {}).get(str(preset_id), "Custom")
                        else:
                            old_val_str = "Default"
                else:
                    if orig_rule:
                        old_val_str = get_old_greeting_name(orig_rule, legacy_type)

                act = next((a for a in actions if a.get('type') == act_type), None)
                if not act:
                    act = {"type": act_type}
                    actions.append(act)

                if new_val in ['off', 'none', 'disable', 'disabled']:
                    if act.get('enabled') is not False:
                        act['enabled'] = False
                        if 'greeting' not in act:
                            act['greeting'] = {"effectiveGreetingType": "Default"}
                        vir_needs_update = True
                    if check_diff(changes, col_name, old_val_str, "Off"):
                        vir_needs_update = True
                    return

                act['enabled'] = True
                matched_id = None
                if new_val == 'default':
                    matched_id = preset_dict.get(dict_type, {}).get('default')
                else:
                    matched_id = preset_dict.get(dict_type, {}).get(new_val)
                    if dict_type == 'InterruptPrompt' and not matched_id:
                        for name, gid in preset_dict['InterruptPrompt'].items():
                            if "patience" in new_val and "patience" in name: matched_id = gid
                            elif "volume" in new_val and "volume" in name: matched_id = gid
                            elif "busy" in new_val and "busy" in name: matched_id = gid
                            elif "important" in new_val and "important" in name: matched_id = gid

                if matched_id:
                    act['greeting'] = {
                        "effectiveGreetingType": "Preset",
                        "preset": { "id": str(matched_id) }
                    }
                    vir_needs_update = True
                    new_val_str = preset_dict.get(dict_type, {}).get(str(matched_id), val)
                    check_diff(changes, col_name, old_val_str, new_val_str)

            apply_vir_greeting('Greeting', 'PlayWelcomePromptAction', 'Introductory', 'Introductory')
            apply_vir_greeting('Audio While Connecting', 'PlayConnectingMessageAction', 'ConnectingMessage', 'ConnectingAudio')
            apply_vir_greeting('Hold Music', 'PlayHoldMusicAction', 'HoldMusic', 'HoldMusic')
            apply_vir_greeting('Interrupt Prompt', 'PlayInterruptPromptAction', 'InterruptPrompt', 'InterruptPrompt')

            if vir_needs_update and not is_preview:
                vir_payload = {"dispatching": {"actions": actions}}
                v_succ, v_err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/voice-interaction-rules/business-hours-rule', method='PUT', json_payload=vir_payload, token=token)
                if v_succ:
                    logs.append("Audio Prompts Updated")
                else:
                    has_error = True
                    logs.append(f"Audio Prompts Error: {format_api_error(v_err)}")

        # --- E. AFTER HOURS RULE ---
        ah_fields = ['After Hours Behavior', 'After Hours Destination']
        if any(get_val(row, f) is not None for f in ah_fields):
            get_succ, ah_rule = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/after-hours-rule', method='GET', token=token)
            if get_succ and isinstance(ah_rule, dict):
                orig_ah = copy.deepcopy(ah_rule)
                a_needs_update = False
                
                val_ahb = get_val(row, 'After Hours Behavior')
                if val_ahb is not None: 
                    if val_ahb == 'TransferToExtension':
                        dest_id = _resolve_ext(get_val(row, 'After Hours Destination'))
                        ah_rule['callHandlingAction'] = val_ahb
                        if dest_id: ah_rule['transfer'] = [{'extension': {'id': dest_id}}]
                    else:
                        ah_rule['callHandlingAction'] = val_ahb
                        ah_rule.pop('transfer', None)
                
                if val_ahb is not None:
                    a_needs_update |= check_diff(changes, 'After Hours Behavior', orig_ah.get('callHandlingAction'), ah_rule.get('callHandlingAction'))
                
                old_a_id = _safe_get_ah_transfer_id(orig_ah.get('transfer')) or 'None'
                new_a_id = _safe_get_ah_transfer_id(ah_rule.get('transfer')) or 'None'
                if get_val(row, 'After Hours Destination') is not None:
                    a_needs_update |= check_diff(changes, 'After Hours Dest', ext_id_to_num.get(old_a_id, old_a_id), ext_id_to_num.get(new_a_id, new_a_id))
                
                for field in _READ_ONLY: ah_rule.pop(field, None)
                ah_rule.pop('greetings', None); ah_rule.pop('callers', None); ah_rule.pop('calledNumbers', None)
                
                if a_needs_update and not is_preview:
                    put_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/after-hours-rule', method='PUT', json_payload=ah_rule, token=token)
                    
                    if not put_succ and ('transfer' in str(err) or 'transfer.extension.id' in str(err)):
                        ah_rule.pop('transfer', None)
                        ah_rule['callHandlingAction'] = 'Voicemail'
                        put_succ2, err2 = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/answering-rule/after-hours-rule', method='PUT', json_payload=ah_rule, token=token)
                        if put_succ2: logs.append("After Hours Updated (Invalid transfer stripped & reverted to Voicemail)")
                        else: has_error = True; logs.append(f"After Hours Error: {format_api_error(err2)}")
                        
                    elif put_succ: 
                        logs.append("After Hours Updated")
                    else: 
                        has_error = True
                        logs.append(f"After Hours Error: {format_api_error(err)}")

        # --- F. MEMBERS ---
        val_mems = get_val(row, 'Members (Ext)')
        if val_mems is not None:
            mem_str = val_mems
            mem_exts = [e.strip() for e in mem_str.split(',')] if mem_str else []
            added_ids = []
            for m in mem_exts:
                m_id = _resolve_ext(m)
                if m_id: added_ids.append({"id": m_id})
            
            if added_ids or not mem_str:
                old_mems_str = "None"
                old_mem_list = []
                get_succ, old_mems_resp = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{q_id}/members', method='GET', token=token)
                if get_succ and isinstance(old_mems_resp, dict):
                    old_mem_list = [str(m.get('extensionNumber')) for m in old_mems_resp.get('records', []) if m.get('extensionNumber')]
                    if old_mem_list: old_mems_str = ", ".join(old_mem_list)
                
                new_mems_str = ", ".join(mem_exts) if mem_exts else "None"
                
                if set(old_mem_list) != set(mem_exts):
                    changes.append({"parameter": "Queue Members", "old": old_mems_str, "new": new_mems_str})
                    if not is_preview:
                        mem_payload = {"addedExtensionIds": [a['id'] for a in added_ids]}
                        s_succ, err = safe_api_call(f'/restapi/v1.0/account/~/call-queues/{q_id}/bulk-assign', method='POST', json_payload=mem_payload, token=token)
                        if s_succ: 
                            logs.append("Members Updated")
                        else: 
                            has_error = True
                            logs.append(f"Members Error: {format_api_error(err)}")

        # --- G. VOICEMAIL NOTIFICATIONS ---
        vm_fields = ['Voicemail Notifications', 'Voicemail Notifications Email', 'Queue Email']
        if any(get_val(row, f) is not None for f in vm_fields):
            get_succ, notif = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/notification-settings', method='GET', token=token)
            if get_succ and isinstance(notif, dict):
                orig_notif = copy.deepcopy(notif)
                vm_set = notif.get('voicemails', {})
                v_needs_update = False
                
                val_vn = get_val(row, 'Voicemail Notifications')
                if val_vn is not None:
                    vm_val = val_vn.lower()
                    if vm_val in ['off', 'false', 'no']:
                        vm_set['notifyByEmail'] = False; vm_set['includeAttachment'] = False; vm_set['markAsRead'] = False
                    elif 'read' in vm_val:
                        vm_set['notifyByEmail'] = True; vm_set['includeAttachment'] = True; vm_set['markAsRead'] = True
                    elif 'attach' in vm_val:
                        vm_set['notifyByEmail'] = True; vm_set['includeAttachment'] = True; vm_set['markAsRead'] = False
                    else:
                        vm_set['notifyByEmail'] = True; vm_set['includeAttachment'] = False; vm_set['markAsRead'] = False
                        
                new_emails = []
                val_vne = get_val(row, 'Voicemail Notifications Email')
                if val_vne is not None:
                    new_emails = [e.strip() for e in val_vne.split(',') if e.strip()]
                    
                if vm_set.get('notifyByEmail'):
                    if not new_emails:
                        fallback = get_val(row, 'Queue Email')
                        if fallback:
                            new_emails = [e.strip() for e in fallback.split(',') if e.strip()]
                        else:
                            if notif.get('advancedMode') and vm_set.get('emailAddresses'):
                                new_emails = vm_set.get('emailAddresses')
                            elif not notif.get('advancedMode') and notif.get('emailAddresses'):
                                new_emails = notif.get('emailAddresses')
                            else:
                                vm_set['notifyByEmail'] = False
                                vm_set['includeAttachment'] = False
                                vm_set['markAsRead'] = False
                                
                if not vm_set.get('notifyByEmail'):
                    vm_set['includeAttachment'] = False
                    vm_set['markAsRead'] = False

                new_notif = copy.deepcopy(orig_notif)
                for field in _READ_ONLY: new_notif.pop(field, None)

                if 'voicemails' not in new_notif:
                    new_notif['voicemails'] = {}

                new_notif['voicemails']['notifyByEmail'] = vm_set.get('notifyByEmail', False)
                
                if new_notif.get('advancedMode'):
                    new_notif['voicemails']['emailAddresses'] = new_emails
                else:
                    new_notif['emailAddresses'] = new_emails

                if vm_set.get('notifyByEmail'):
                    new_notif['voicemails']['includeAttachment'] = vm_set.get('includeAttachment', False)
                    new_notif['voicemails']['markAsRead'] = vm_set.get('markAsRead', False)
                else:
                    new_notif['voicemails'].pop('includeAttachment', None)
                    new_notif['voicemails'].pop('markAsRead', None)
                
                old_email_on = str(orig_notif.get('voicemails', {}).get('notifyByEmail'))
                new_email_on = str(vm_set.get('notifyByEmail'))
                if val_vn is not None:
                    v_needs_update |= check_diff(changes, 'VM Email On', old_email_on, new_email_on)
                    v_needs_update |= check_diff(changes, 'VM Attach/Read', str(orig_notif.get('voicemails', {}).get('includeAttachment')), str(vm_set.get('includeAttachment')))
                
                old_emails = orig_notif.get('voicemails', {}).get('emailAddresses', []) if orig_notif.get('advancedMode') else orig_notif.get('emailAddresses', [])
                if val_vne is not None or get_val(row, 'Queue Email') is not None:
                    if set(old_emails) != set(new_emails):
                        v_needs_update |= check_diff(changes, 'VM Emails', ", ".join(old_emails), ", ".join(new_emails))
                
                if v_needs_update and not is_preview:
                    put_succ, err = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/notification-settings', method='PUT', json_payload=new_notif, token=token)
                    
                    if not put_succ and ('includeAttachment' in str(err) or 'markAsRead' in str(err)):
                        new_notif['voicemails'].pop('includeAttachment', None)
                        new_notif['voicemails'].pop('markAsRead', None)
                        
                        put_succ2, err2 = safe_api_call(f'/restapi/v1.0/account/~/extension/{q_id}/notification-settings', method='PUT', json_payload=new_notif, token=token)
                        if put_succ2: 
                            logs.append("Notifications Updated (Attachments popped due to account limits)")
                        else: 
                            has_error = True
                            logs.append(f"Notifications Error: {format_api_error(err2)}")
                    elif put_succ: 
                        logs.append("Notifications Updated")
                    else: 
                        has_error = True
                        logs.append(f"Notifications Error: {format_api_error(err)}")

        if not logs and not changes: 
            res_dict = {"ext": ext_num, "status": "info", "message": "No valid changes found in row.", "changes": changes}
        elif has_error: 
            res_dict = {"ext": ext_num, "status": "error", "message": " | ".join(logs) or "Unknown Error", "changes": changes}
        else: 
            res_dict = {"ext": ext_num, "status": "success", "message": "Evaluated successfully." if is_preview else "Changes synced.", "changes": changes}
            
        yield {"type": "progress", "current": i + 1, "total": total_records, "result": res_dict, "is_preview": is_preview}
        time.sleep(1.5) 
            
    yield {"type": "done", "is_preview": is_preview}
