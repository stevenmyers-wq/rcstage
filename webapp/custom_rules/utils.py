import pandas as pd
from datetime import datetime

def parse_time_range(range_str):
    """Parses '8:00 AM - 5:00 PM' into API format."""
    if not isinstance(range_str, str) or '-' not in range_str:
        return None
    try:
        start_str, end_str = range_str.split('-')
        fmt_in = "%I:%M %p" 
        fmt_out = "%H:%M"
        start_time = datetime.strptime(start_str.strip(), fmt_in).strftime(fmt_out)
        end_time = datetime.strptime(end_str.strip(), fmt_in).strftime(fmt_out)
        return [{"from": start_time, "to": end_time}]
    except:
        return None

def build_rule_payload(row, ext_id):
    """Constructs the V1 API payload from a CSV row."""
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    
    # Handle Enabled/Disabled logic
    enabled_val = str(row.get('Enabled', 'Yes')).lower()
    enabled = True if enabled_val in ['yes', 'true', '1', 'on'] else False
    
    payload = {
        "type": "Custom", 
        "name": rule_name, 
        "enabled": enabled
    }

    # --- 1. Caller ID Condition ---
    if pd.notna(row.get('Caller ID')):
        callers = [{'callerId': c.strip()} for c in str(row.get('Caller ID')).split(',') if c.strip()]
        if callers:
            payload['callers'] = callers

    # --- 2. Called Number Condition ---
    if pd.notna(row.get('Called Number')):
        called_numbers = [{'phoneNumber': n.strip()} for n in str(row.get('Called Number')).split(',') if n.strip()]
        if called_numbers:
            payload['calledNumbers'] = called_numbers

    # --- 3. Schedule Condition ---
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
    
    if has_schedule:
        payload['schedule'] = schedule

    # --- 4. Actions ---
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

def transform_v1_to_v2(v1_payload):
    """
    Converts a standard V1 Answering Rule payload into a V2 Interaction Rule payload.
    Required for accounts with 'New Call Handling' enabled.
    """
    v2_payload = {
        "name": v1_payload.get("name"),
        "enabled": v1_payload.get("enabled"),
        "conditions": {},
        "actions": []
    }

    # 1. Move Conditions into 'conditions' object
    if "callers" in v1_payload:
        v2_payload["conditions"]["callers"] = v1_payload["callers"]
    if "calledNumbers" in v1_payload:
        v2_payload["conditions"]["calledNumbers"] = v1_payload["calledNumbers"]
    if "schedule" in v1_payload:
        v2_payload["conditions"]["schedule"] = v1_payload["schedule"]

    # 2. Map Actions to 'actions' array
    v1_action = v1_payload.get("callHandlingAction")
    
    action_obj = {}

    if v1_action == "UnconditionalForwarding":
        action_obj["type"] = "UnconditionalForwarding"
        if "unconditionalForwarding" in v1_payload:
             action_obj["phoneNumber"] = v1_payload["unconditionalForwarding"].get("phoneNumber")

    elif v1_action == "TransferToExtension":
        action_obj["type"] = "Transfer"
        if "transfer" in v1_payload:
            action_obj["extension"] = v1_payload["transfer"].get("extension")

    elif v1_action == "TakeMessagesOnly":
        action_obj["type"] = "Voicemail"
        if "voicemail" in v1_payload:
            # V2 usually expects 'extension' or 'recipient' inside
            action_obj["extension"] = v1_payload["voicemail"].get("recipient")

    elif v1_action == "PlayAnnouncementOnly":
        action_obj["type"] = "PlayAnnouncement"
        # Note: PlayAnnouncement in V2 usually requires an ID. 
        # If V1 didn't have one (default greeting), this might be tricky, 
        # but we send the type to let RC handle defaults.

    else:
        # Default fallback (ForwardCalls)
        action_obj["type"] = "ForwardCalls"

    v2_payload["actions"].append(action_obj)
    
    return v2_payload
