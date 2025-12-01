import pandas as pd
import re
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

def format_phone(phone_val):
    """
    Ensures phone numbers are in E.164 format (starts with +).
    """
    if pd.isna(phone_val): return None
    
    # 1. Clean string (remove spaces, dashes, parens, dots)
    # This turns "61400.0" -> "61400"
    raw_str = str(phone_val).split('.')[0].strip()
    clean_num = re.sub(r'[^\d+]', '', raw_str)
    
    if not clean_num: return None
    
    # 2. Add + if missing and looks like international
    if len(clean_num) > 9 and not clean_num.startswith('+'):
        return f"+{clean_num}"
    
    return clean_num

def build_v1_payload(row, ext_id):
    """
    Constructs the standard V1 Payload from CSV.
    We use this as the 'base' data, then convert to V2 if needed in routes.py.
    """
    rule_name = row.get('Rule Name', f'Custom Rule {datetime.now()}')
    
    enabled_val = str(row.get('Enabled', 'Yes')).lower()
    enabled = enabled_val in ['yes', 'true', '1', 'on']
    
    payload = {
        "type": "Custom", 
        "name": rule_name, 
        "enabled": enabled
    }

    # 1. Caller ID
    if pd.notna(row.get('Caller ID')):
        raw_callers = str(row.get('Caller ID')).split(',')
        callers = []
        for c in raw_callers:
            fmt = format_phone(c)
            if fmt: callers.append({'callerId': fmt})
        if callers:
            payload['callers'] = callers

    # 2. Called Number
    if pd.notna(row.get('Called Number')):
        raw_called = str(row.get('Called Number')).split(',')
        called = []
        for n in raw_called:
            fmt = format_phone(n)
            if fmt: called.append({'phoneNumber': fmt})
        if called:
            payload['calledNumbers'] = called

    # 3. Schedule
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

    # 4. Actions
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
