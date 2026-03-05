import pandas as pd
import re
from datetime import datetime

# --- 1. BASIC FORMATTERS ---

def parse_time_range(range_str):
    """Parses '8:00 AM - 5:00 PM' or multiple '8:00 AM - 12:00 PM, 1:00 PM - 5:00 PM' into API format."""
    if pd.isna(range_str) or not str(range_str).strip(): return None
    try:
        ranges = []
        for part in str(range_str).split(','):
            if '-' not in part: continue
            start, end = part.split('-')
            fmt_in, fmt_out = "%I:%M %p", "%H:%M"
            ranges.append({
                "from": datetime.strptime(start.strip(), fmt_in).strftime(fmt_out),
                "to": datetime.strptime(end.strip(), fmt_in).strftime(fmt_out)
            })
        return ranges if ranges else None
    except: 
        return None

def parse_specific_dates(date_str):
    """Parses '2024-12-25 00:00 to 2024-12-26 23:59' into API format."""
    if pd.isna(date_str) or not str(date_str).strip(): return None
    try:
        ranges = []
        for part in str(date_str).split(','):
            if ' to ' not in part: continue
            start, end = part.split(' to ')
            fmt_in, fmt_out = "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S.000Z"
            ranges.append({
                "from": datetime.strptime(start.strip(), fmt_in).strftime(fmt_out),
                "to": datetime.strptime(end.strip(), fmt_in).strftime(fmt_out)
            })
        return ranges if ranges else None
    except:
        return None

def format_phone(phone_val):
    """Ensures phone numbers are in E.164 format."""
    if pd.isna(phone_val): return None
    raw_str = str(phone_val).split('.')[0].strip()
    clean_num = re.sub(r'[^\d+]', '', raw_str)
    if not clean_num: return None
    
    # Check for local Australian number formats (e.g., 0412 123 123 or 02 9999 0000)
    if clean_num.startswith('0') and len(clean_num) == 10:
        return f"+61{clean_num[1:]}"
        
    if len(clean_num) > 9 and not clean_num.startswith('+'): 
        return f"+{clean_num}"
    return clean_num

def format_time_display(ranges):
    """Converts API time ranges [{'from': '09:00', 'to': '17:00'}] to '9:00 AM - 5:00 PM'"""
    if not ranges: return ""
    display_strs = []
    for r in ranges:
        try:
            t_from = datetime.strptime(r['from'], "%H:%M").strftime("%-I:%M %p")
            t_to = datetime.strptime(r['to'], "%H:%M").strftime("%-I:%M %p")
            display_strs.append(f"{t_from} - {t_to}")
        except:
            display_strs.append(f"{r['from']} - {r['to']}")
    return ", ".join(display_strs)

# --- 2. V1 PAYLOAD BUILDER ---

def build_v1_payload(row, ext_id):
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    enabled_val = str(row.get('Enabled', 'Yes')).lower()
    enabled = enabled_val in ['yes', 'true', '1', 'on']
    
    payload = {"type": "Custom", "name": rule_name, "enabled": enabled}

    if pd.notna(row.get('Caller ID')):
        raw_callers = str(row.get('Caller ID')).split(',')
        callers = []
        for c in raw_callers:
            fmt = format_phone(c)
            if fmt: callers.append({'callerId': fmt})
        if callers: payload['callers'] = callers

    if pd.notna(row.get('Called Number')):
        raw_called = str(row.get('Called Number')).split(',')
        called = []
        for n in raw_called:
            fmt = format_phone(n)
            if fmt: called.append({'phoneNumber': fmt})
        if called: payload['calledNumbers'] = called

    schedule = {}
    weekly_ranges = {}
    has_schedule = False
    days_map = {
        'Monday': 'monday', 'Tuesday': 'tuesday', 'Wednesday': 'wednesday',
        'Thursday': 'thursday', 'Friday': 'friday', 'Saturday': 'saturday', 'Sunday': 'sunday'
    }
    
    # Process Weekly Ranges
    for col, api_key in days_map.items():
        if col in row and pd.notna(row[col]):
            ranges = parse_time_range(row[col])
            if ranges:
                weekly_ranges[api_key] = ranges
                has_schedule = True
                
    if weekly_ranges:
        schedule['weeklyRanges'] = weekly_ranges

    # Process Specific Dates
    if 'Specific Dates' in row and pd.notna(row['Specific Dates']):
        date_ranges = parse_specific_dates(row['Specific Dates'])
        if date_ranges:
            schedule['ranges'] = date_ranges
            has_schedule = True
    
    if has_schedule: 
        payload['schedule'] = schedule

    action_map = {
        'Transfer to External': 'UnconditionalForwarding',
        'Send to Voicemail': 'TakeMessagesOnly',
        'Transfer to Extension': 'TransferToExtension',
        'Play Message': 'PlayAnnouncementOnly',
        'Play Message and Disconnect': 'PlayAnnouncementOnly',
        'Fwd Direct To Main': 'ForwardCalls'
    }
    user_action = row.get('Action')
    api_action = action_map.get(user_action, 'ForwardCalls')
    payload['callHandlingAction'] = api_action
    return payload, api_action

# --- 3. V2 TRANSFORMER ---

def transform_v1_to_v2(v1_payload, owner_ext_id, user_devices=None):
    if user_devices is None: user_devices = []
    v2 = {
        "displayName": str(v1_payload.get("name", f"Custom Rule {datetime.now()}")), 
        "enabled": v1_payload.get("enabled", True),
        "conditions": [],
        "dispatching": {"type": "Terminate", "actions": []}
    }
    
    # --- 1. Conditions (Interaction) ---
    # CRITICAL FIX: The V2 API demands the 'from' parameter exist inside the 
    # Interaction block, even if it is completely empty.
    interaction_cond = {
        "type": "Interaction",
        "from": []
    }
    
    if "calledNumbers" in v1_payload and v1_payload["calledNumbers"]:
        interaction_cond["to"] = [{"phoneNumber": item['phoneNumber']} for item in v1_payload['calledNumbers']]
        
    if "callers" in v1_payload and v1_payload["callers"]:
        interaction_cond["from"] = [{"phoneNumber": item['callerId']} for item in v1_payload['callers']]
        
    v2["conditions"].append(interaction_cond)

    # --- 2. Conditions (Schedule) ---
    if "schedule" in v1_payload:
        v2["conditions"].append({
            "type": "Schedule",
            "schedule": v1_payload["schedule"]
        })

    # --- 3. Actions - Strict Schema ---
    v1_act = v1_payload.get("callHandlingAction")
    vm_prompt = {"greeting": {"effectiveGreetingType": "Preset", "preset": {"id": "590080"}}}

    if v1_act == "ForwardCalls":
        v2["dispatching"]["type"] = "RingAndTerminate"
        actions = []
        actions.append({"type": "RingGroupAction", "enabled": False, "targets": [{"type": "AllMobileRingTarget", "name": "My mobile apps"}], "duration": 20})
        actions.append({"type": "RingGroupAction", "enabled": False, "targets": [{"type": "AllDesktopRingTarget", "name": "My desktop"}], "duration": 20})
        for dev in user_devices:
            actions.append({"type": "RingGroupAction", "enabled": False, "targets": [{"type": "DeviceRingTarget", "device": {"id": dev['id']}}], "duration": 20})
        
        actions.append({
            "type": "TerminatingAction",
            "targets": [{
                "type": "VoiceMailTerminatingTarget",
                "mailbox": {"id": owner_ext_id},
                "dispatchingType": "Terminating",
                "prompt": vm_prompt
            }]
        })
        v2["dispatching"]["actions"] = actions

    elif v1_act == "UnconditionalForwarding":
        dest_num = v1_payload.get("unconditionalForwarding", {}).get("phoneNumber")
        formatted_dest = format_phone(dest_num)
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [{
                "type": "PhoneNumberTerminatingTarget",
                "destination": {"phoneNumber": formatted_dest},
                "dispatchingType": "Terminating"
            }]
        })

    elif v1_act == "TransferToExtension":
        target_ext_id = v1_payload.get("transfer", {}).get("extension", {}).get("id")
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [{
                "type": "ExtensionTerminatingTarget",
                "extension": {"id": target_ext_id},
                "dispatchingType": "Terminating"
            }]
        })

    elif v1_act == "TakeMessagesOnly":
        vm_recipient_id = v1_payload.get("voicemail", {}).get("recipient", {}).get("id")
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [{
                "type": "VoiceMailTerminatingTarget",
                "mailbox": {"id": vm_recipient_id},
                "prompt": vm_prompt,
                "dispatchingType": "Terminating"
            }]
        })

    elif v1_act == "PlayAnnouncementOnly":
         v2["dispatching"]["actions"].append({
             "type": "TerminatingAction",
             "targets": [{
                 "type": "PlayAnnouncementTerminatingTarget",
                 "prompt": vm_prompt,
                 "dispatchingType": "Terminating"
             }]
         })

    return v2

# --- 4. AUDIT PARSER ---

def parse_rule_to_row(ext, rule, is_v2=False):
    """Converts a RingCentral Rule (V1 or V2) into a flat Excel row."""
    row = {
        'Ext Number': ext.get('extensionNumber'),
        'Ext Name': ext.get('name'),
        'Rule ID': rule.get('id'),
        'Rule Name': rule.get('name') or rule.get('displayName'),
        'Enabled': 'Yes' if rule.get('enabled') else 'No',
        'Caller ID': '', 'Called Number': '', 
        'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': '', 'Sunday': '',
        'Specific Dates': '',
        'Action': 'Unknown',
        'External Number': '', 'Transfer Extension': '', 'Voicemail Recipient': ''
    }

    schedule_data = None

    if is_v2:
        for cond in rule.get('conditions', []):
            if cond.get('type') == 'Interaction':
                if 'from' in cond:
                    row['Caller ID'] = ', '.join([str(c.get('phoneNumber', c)) for c in cond['from']])
                if 'to' in cond:
                    row['Called Number'] = ', '.join([str(t.get('phoneNumber', t)) for t in cond['to']])
            elif cond.get('type') == 'Schedule':
                schedule_data = cond.get('schedule', {})
    else:
        if 'callers' in rule:
            row['Caller ID'] = ', '.join([c.get('callerId') for c in rule['callers']])
        if 'calledNumbers' in rule:
            row['Called Number'] = ', '.join([c.get('phoneNumber') for c in rule['calledNumbers']])
        if 'schedule' in rule:
            schedule_data = rule['schedule']

    if schedule_data:
        weekly = schedule_data.get('weeklyRanges', {})
        for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
            if day in weekly:
                row[day.capitalize()] = format_time_display(weekly[day])
        
        ranges = schedule_data.get('ranges', [])
        if ranges:
            date_strs = []
            for r in ranges:
                try:
                    dt_from = datetime.fromisoformat(r['from'].replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    dt_to = datetime.fromisoformat(r['to'].replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    date_strs.append(f"{dt_from} to {dt_to}")
                except:
                    date_strs.append(f"{r['from']} to {r['to']}")
            row['Specific Dates'] = "\n".join(date_strs)

    if is_v2:
        actions = rule.get('dispatching', {}).get('actions', [])
        term_action = next((a for a in actions if a.get('type') == 'TerminatingAction'), None)
        
        if term_action:
            target_type = term_action.get('terminatingTargetType')
            targets = term_action.get('targets', [])
            main_target = next((t for t in targets if t.get('type') == target_type), None)

            if target_type == 'PhoneNumberTerminatingTarget':
                row['Action'] = 'Transfer to External'
                if main_target: row['External Number'] = main_target.get('destination', {}).get('phoneNumber')
            elif target_type == 'ExtensionTerminatingTarget':
                row['Action'] = 'Transfer to Extension'
                if main_target: row['Transfer Extension'] = main_target.get('extension', {}).get('id')
            elif target_type == 'VoiceMailTerminatingTarget':
                row['Action'] = 'Send to Voicemail'
                if main_target: row['Voicemail Recipient'] = main_target.get('mailbox', {}).get('id')
            elif target_type == 'PlayAnnouncementTerminatingTarget':
                row['Action'] = 'Play Message'
    else:
        action_type = rule.get('callHandlingAction')
        if action_type == 'UnconditionalForwarding':
            row['Action'] = 'Transfer to External'
            row['External Number'] = rule.get('unconditionalForwarding', {}).get('phoneNumber')
        elif action_type == 'TransferToExtension':
            row['Action'] = 'Transfer to Extension'
            row['Transfer Extension'] = rule.get('transfer', {}).get('extension', {}).get('extensionNumber')
        elif action_type == 'TakeMessagesOnly':
            row['Action'] = 'Send to Voicemail'
            row['Voicemail Recipient'] = rule.get('voicemail', {}).get('recipient', {}).get('id')
        elif action_type == 'PlayAnnouncementOnly':
            row['Action'] = 'Play Message'

    return row
