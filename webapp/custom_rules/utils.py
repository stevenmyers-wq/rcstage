import pandas as pd
import re
from datetime import datetime

# --- EXISTING HELPERS (Keep these) ---
def parse_time_range(range_str):
    if not isinstance(range_str, str) or '-' not in range_str: return None
    try:
        start, end = range_str.split('-')
        fmt_in, fmt_out = "%I:%M %p", "%H:%M"
        return [{"from": datetime.strptime(start.strip(), fmt_in).strftime(fmt_out),
                 "to": datetime.strptime(end.strip(), fmt_in).strftime(fmt_out)}]
    except: return None

def format_phone(phone_val):
    if pd.isna(phone_val): return None
    raw_str = str(phone_val).split('.')[0].strip()
    clean_num = re.sub(r'[^\d+]', '', raw_str)
    if not clean_num: return None
    if len(clean_num) > 9 and not clean_num.startswith('+'): return f"+{clean_num}"
    return clean_num

def build_v1_payload(row, ext_id):
    # ... (Keep your existing build_v1_payload code exactly as is) ...
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
    # ... (Schedule logic) ...
    # ... (Actions logic) ...
    # For brevity, I assume you kept the existing function here.
    # Re-paste the Action Logic if you need me to provide the full file.
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

# --- NEW: AUDIT HELPERS ---

def parse_rule_to_row(ext, rule, is_v2=False):
    """
    Converts a RingCentral Rule (V1 or V2) into a flat Excel row.
    """
    row = {
        'Ext Number': ext.get('extensionNumber'),
        'Ext Name': ext.get('name'),
        'Rule ID': rule.get('id'),
        'Rule Name': rule.get('name') or rule.get('displayName'),
        'Enabled': 'Yes' if rule.get('enabled') else 'No',
        'Caller ID': '',
        'Called Number': '',
        'Action': 'Unknown',
        'External Number': '',
        'Transfer Extension': '',
        'Voicemail Recipient': ''
    }

    # --- 1. PARSE CONDITIONS ---
    if is_v2:
        # V2 Conditions are in a list
        for cond in rule.get('conditions', []):
            if cond.get('type') == 'Interaction':
                if 'from' in cond:
                    row['Caller ID'] = ', '.join([c.get('phoneNumber', c) for c in cond['from']])
                if 'to' in cond:
                    # In V2 'to' might be IDs or Strings depending on the fetch depth. 
                    # Usually strings in the list view.
                    row['Called Number'] = ', '.join([t for t in cond['to']])
    else:
        # V1 Conditions are keys
        if 'callers' in rule:
            row['Caller ID'] = ', '.join([c.get('callerId') for c in rule['callers']])
        if 'calledNumbers' in rule:
            row['Called Number'] = ', '.join([c.get('phoneNumber') for c in rule['calledNumbers']])

    # --- 2. PARSE ACTIONS ---
    if is_v2:
        # V2 Actions are deep in dispatching
        actions = rule.get('dispatching', {}).get('actions', [])
        # Find the primary terminating action
        term_action = next((a for a in actions if a.get('type') == 'TerminatingAction'), None)
        
        if term_action:
            target_type = term_action.get('terminatingTargetType')
            targets = term_action.get('targets', [])
            # Find the target that matches the type
            main_target = next((t for t in targets if t.get('type') == target_type), None)

            if target_type == 'PhoneNumberTerminatingTarget':
                row['Action'] = 'Transfer to External'
                if main_target:
                    row['External Number'] = main_target.get('destination', {}).get('phoneNumber')
            
            elif target_type == 'ExtensionTerminatingTarget':
                row['Action'] = 'Transfer to Extension'
                if main_target:
                    row['Transfer Extension'] = main_target.get('extension', {}).get('id') # ID is often all we get
            
            elif target_type == 'VoiceMailTerminatingTarget':
                row['Action'] = 'Send to Voicemail'
                if main_target:
                    row['Voicemail Recipient'] = main_target.get('mailbox', {}).get('id')
            
            elif target_type == 'PlayAnnouncementTerminatingTarget':
                row['Action'] = 'Play Message'
    else:
        # V1 Actions
        action_type = rule.get('callHandlingAction')
        if action_type == 'UnconditionalForwarding':
            row['Action'] = 'Transfer to External'
            row['External Number'] = rule.get('unconditionalForwarding', {}).get('phoneNumber')
        elif action_type == 'TransferToExtension':
            row['Action'] = 'Transfer to Extension'
            row['Transfer Extension'] = rule.get('transfer', {}).get('extension', {}).get('extensionNumber')
        elif action_type == 'TakeMessagesOnly':
            row['Action'] = 'Send to Voicemail'
            # Try to get recipient extension number if available
            row['Voicemail Recipient'] = rule.get('voicemail', {}).get('recipient', {}).get('id')
        elif action_type == 'PlayAnnouncementOnly':
            row['Action'] = 'Play Message'

    return row
