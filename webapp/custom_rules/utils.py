import pandas as pd
import re
from datetime import datetime

# --- 1. BASIC FORMATTERS ---

def parse_time_range(range_str):
    """Parses '8:00 AM - 5:00 PM' into API format."""
    if not isinstance(range_str, str) or '-' not in range_str: return None
    try:
        start, end = range_str.split('-')
        fmt_in, fmt_out = "%I:%M %p", "%H:%M"
        return [{"from": datetime.strptime(start.strip(), fmt_in).strftime(fmt_out),
                 "to": datetime.strptime(end.strip(), fmt_in).strftime(fmt_out)}]
    except: return None

def format_phone(phone_val):
    """Ensures phone numbers are in E.164 format (starts with +)."""
    if pd.isna(phone_val): return None
    raw_str = str(phone_val).split('.')[0].strip()
    clean_num = re.sub(r'[^\d+]', '', raw_str)
    if not clean_num: return None
    if len(clean_num) > 9 and not clean_num.startswith('+'): return f"+{clean_num}"
    return clean_num

# --- 2. V1 PAYLOAD BUILDER ---

def build_v1_payload(row, ext_id):
    """Constructs the standard V1 Payload from CSV."""
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    enabled_val = str(row.get('Enabled', 'Yes')).lower()
    enabled = enabled_val in ['yes', 'true', '1', 'on']
    
    payload = {"type": "Custom", "name": rule_name, "enabled": enabled}

    # Caller ID
    if pd.notna(row.get('Caller ID')):
        raw_callers = str(row.get('Caller ID')).split(',')
        callers = []
        for c in raw_callers:
            fmt = format_phone(c)
            if fmt: callers.append({'callerId': fmt})
        if callers: payload['callers'] = callers

    # Called Number
    if pd.notna(row.get('Called Number')):
        raw_called = str(row.get('Called Number')).split(',')
        called = []
        for n in raw_called:
            fmt = format_phone(n)
            if fmt: called.append({'phoneNumber': fmt})
        if called: payload['calledNumbers'] = called

    # Schedule
    schedule = {'weeklyRanges': {}}
    days_map = {
        'Monday': 'monday', 'Tuesday': 'tuesday', 'Wednesday': 'wednesday',
        'Thursday': 'thursday', 'Friday': 'friday', 'Saturday': 'saturday', 'Sunday': 'sunday'
    }
    has_schedule = False
    for col, api_key in days_map.items():
        if col in row and pd.notna(row[col]):
            ranges = parse_time_range(row[col])
            if ranges:
                schedule['weeklyRanges'][api_key] = ranges
                has_schedule = True
    
    if has_schedule: payload['schedule'] = schedule

    # Actions
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

# --- 3. V2 TRANSFORMER (The Missing Function) ---

def transform_v1_to_v2(v1_payload, owner_ext_id, user_devices=None):
    """
    Reconstructs V1 data into V2 Interaction Rule format.
    Includes logic to inject User Devices (for CMN-100) and Dummy Targets (CHF-211/212).
    """
    if user_devices is None: user_devices = []

    v2 = {
        "displayName": v1_payload.get("name"), 
        "enabled": v1_payload.get("enabled"),
        "conditions": [],
        "dispatching": {
            "type": "Terminate",
            "actions": []
        }
    }
    
    # Conditions
    interaction_cond = {
        "type": "Interaction",
        "to": [],
        "from": []
    }
    if "calledNumbers" in v1_payload:
        interaction_cond["to"] = [item['phoneNumber'] for item in v1_payload['calledNumbers']]
    if "callers" in v1_payload:
        interaction_cond["from"] = [item['callerId'] for item in v1_payload['callers']]
    v2["conditions"].append(interaction_cond)

    # Actions - Inject Dummies
    # 1. Mobile Apps
    v2["dispatching"]["actions"].append({
        "type": "RingGroupAction",
        "enabled": False, 
        "targets": [{"type": "AllMobileRingTarget", "name": "My mobile apps"}],
        "duration": 20
    })
    # 2. Desktop Apps
    v2["dispatching"]["actions"].append({
        "type": "RingGroupAction",
        "enabled": False, 
        "targets": [{"type": "AllDesktopRingTarget", "name": "My desktop"}],
        "duration": 20
    })
    # 3. User Devices (CMN-100)
    for dev in user_devices:
        v2["dispatching"]["actions"].append({
            "type": "RingGroupAction",
            "enabled": False,
            "targets": [{
                "type": "DeviceRingTarget",
                "device": {"id": dev['id']}
            }],
            "duration": 20
        })

    # Actions - Real Logic
    v1_act = v1_payload.get("callHandlingAction")
    
    vm_prompt = {
        "greeting": {
            "effectiveGreetingType": "Preset",
            "preset": {"id": "590080"} 
        }
    }

    fallback_vm_target = {
        "type": "VoiceMailTerminatingTarget",
        "mailbox": {"id": owner_ext_id},
        "prompt": vm_prompt 
    }

    # Case A: Unconditional Forwarding
    if v1_act == "UnconditionalForwarding":
        dest_num = v1_payload.get("unconditionalForwarding", {}).get("phoneNumber")
        formatted_dest = format_phone(dest_num)
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "PhoneNumberTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                fallback_vm_target,
                {
                    "type": "PhoneNumberTerminatingTarget",
                    "destination": {"phoneNumber": formatted_dest},
                    "dispatchingType": "Terminating" 
                }
            ]
        }
        v2["dispatching"]["actions"].append(action)

    # Case B: Transfer to Extension
    elif v1_act == "TransferToExtension":
        target_ext_id = v1_payload.get("transfer", {}).get("extension", {}).get("id")
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "ExtensionTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                fallback_vm_target,
                {
                    "type": "ExtensionTerminatingTarget",
                    "extension": {"id": target_ext_id},
                    "dispatchingType": "Terminating"
                }
            ]
        }
        v2["dispatching"]["actions"].append(action)

    # Case C: Voicemail
    elif v1_act == "TakeMessagesOnly":
        vm_recipient_id = v1_payload.get("voicemail", {}).get("recipient", {}).get("id")
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "VoiceMailTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                {
                    "type": "VoiceMailTerminatingTarget",
                    "mailbox": {"id": vm_recipient_id},
                    "dispatchingType": "Terminating",
                    "prompt": vm_prompt
                }
            ]
        }
        v2["dispatching"]["actions"].append(action)
        
    # Case D: Play Announcement
    elif v1_act == "PlayAnnouncementOnly":
         action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "PlayAnnouncementTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                fallback_vm_target,
                {
                     "type": "PlayAnnouncementTerminatingTarget",
                     "dispatchingType": "Terminating",
                     "prompt": vm_prompt 
                }
            ]
         }
         v2["dispatching"]["actions"].append(action)

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
        'Caller ID': '', 'Called Number': '', 'Action': 'Unknown',
        'External Number': '', 'Transfer Extension': '', 'Voicemail Recipient': ''
    }

    if is_v2:
        for cond in rule.get('conditions', []):
            if cond.get('type') == 'Interaction':
                if 'from' in cond:
                    row['Caller ID'] = ', '.join([str(c.get('phoneNumber', c)) for c in cond['from']])
                if 'to' in cond:
                    row['Called Number'] = ', '.join([str(t) for t in cond['to']])
        
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
        if 'callers' in rule:
            row['Caller ID'] = ', '.join([c.get('callerId') for c in rule['callers']])
        if 'calledNumbers' in rule:
            row['Called Number'] = ', '.join([c.get('phoneNumber') for c in rule['calledNumbers']])
            
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
