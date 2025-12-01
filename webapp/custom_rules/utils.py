import pandas as pd
from datetime import datetime

def parse_time_range(range_str):
    if not isinstance(range_str, str) or '-' not in range_str: return None
    try:
        start, end = range_str.split('-')
        fmt_in, fmt_out = "%I:%M %p", "%H:%M"
        return [{"from": datetime.strptime(start.strip(), fmt_in).strftime(fmt_out),
                 "to": datetime.strptime(end.strip(), fmt_in).strftime(fmt_out)}]
    except: return None

def build_rule_payload(row, ext_id):
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    enabled = str(row.get('Enabled', 'Yes')).lower() in ['yes', 'true', '1', 'on']
    
    payload = {"type": "Custom", "name": rule_name, "enabled": enabled}

    # 1. Caller ID
    if pd.notna(row.get('Caller ID')):
        callers = [{'callerId': c.strip()} for c in str(row.get('Caller ID')).split(',') if c.strip()]
        if callers: payload['callers'] = callers

    # 2. Called Number
    if pd.notna(row.get('Called Number')):
        called = [{'phoneNumber': n.strip()} for n in str(row.get('Called Number')).split(',') if n.strip()]
        if called: payload['calledNumbers'] = called

    # 3. Schedule
    schedule = {'weeklyRanges': {}}
    days = {'Monday':'monday', 'Tuesday':'tuesday', 'Wednesday':'wednesday', 'Thursday':'thursday', 'Friday':'friday', 'Saturday':'saturday', 'Sunday':'sunday'}
    has_schedule = False
    for col, key in days.items():
        if pd.notna(row.get(col)):
            ranges = parse_time_range(row.get(col))
            if ranges:
                schedule['weeklyRanges'][key] = ranges
                has_schedule = True
    if has_schedule: payload['schedule'] = schedule

    # Actions
    action_map = {
        'Transfer to External': 'UnconditionalForwarding',
        'Send to Voicemail': 'TakeMessagesOnly',
        'Transfer to Extension': 'TransferToExtension',
        'Play Message': 'PlayAnnouncementOnly',
        'Play Message and Disconnect': 'PlayAnnouncementOnly'
    }
    api_action = action_map.get(row.get('Action'), 'ForwardCalls')
    payload['callHandlingAction'] = api_action
    
    return payload, api_action

def transform_v1_to_v2(v1):
    v2 = {"name": v1.get("name"), "enabled": v1.get("enabled"), "conditions": {}, "actions": []}
    
    # Map Conditions
    if "callers" in v1: v2["conditions"]["callers"] = v1["callers"]
    if "calledNumbers" in v1: v2["conditions"]["calledNumbers"] = v1["calledNumbers"]
    if "schedule" in v1: v2["conditions"]["schedule"] = v1["schedule"]

    # Map Actions
    v1_act = v1.get("callHandlingAction")
    act_obj = {"type": "ForwardCalls"} # Default

    if v1_act == "UnconditionalForwarding":
        act_obj["type"] = "UnconditionalForwarding"
        if "unconditionalForwarding" in v1: act_obj["phoneNumber"] = v1["unconditionalForwarding"].get("phoneNumber")
    elif v1_act == "TransferToExtension":
        act_obj["type"] = "Transfer"
        if "transfer" in v1: act_obj["extension"] = v1["transfer"].get("extension")
    elif v1_act == "TakeMessagesOnly":
        act_obj["type"] = "Voicemail"
        if "voicemail" in v1: act_obj["extension"] = v1["voicemail"].get("recipient")
    elif v1_act == "PlayAnnouncementOnly":
        act_obj["type"] = "PlayAnnouncement"

    v2["actions"].append(act_obj)
    return v2
