import pandas as pd
from datetime import datetime

def parse_time_range(range_str):
    """Parses '8:00 AM - 5:00 PM' into API format."""
    if not isinstance(range_str, str) or '-' not in range_str:
        return None
    try:
        start_str, end_str = range_str.split('-')
        # Adjust format if your CSV uses different time formats
        fmt_in = "%I:%M %p" 
        fmt_out = "%H:%M"
        start_time = datetime.strptime(start_str.strip(), fmt_in).strftime(fmt_out)
        end_time = datetime.strptime(end_str.strip(), fmt_in).strftime(fmt_out)
        return [{"from": start_time, "to": end_time}]
    except:
        return None

def build_rule_payload(row, ext_id):
    """Constructs the API payload from a CSV row."""
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    
    # Handle Enabled/Disabled logic
    enabled_val = str(row.get('Enabled', 'Yes')).lower()
    enabled = True if enabled_val in ['yes', 'true', '1', 'on'] else False
    
    # FIXED: Do not initialize empty lists/dicts here. 
    # RingCentral rejects empty 'schedule' objects.
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
    
    # Only add the schedule object if we actually found valid times
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
    
    # Default to ForwardCalls if action is missing/typo
    user_action = row.get('Action')
    api_action = action_map.get(user_action, 'ForwardCalls')
    payload['callHandlingAction'] = api_action
    
    return payload, api_action
