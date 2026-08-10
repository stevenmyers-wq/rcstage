import pandas as pd
import re
from datetime import datetime
from webapp.rc_api import rc_api_call

# --- 0. EXTENSION TYPE / SITE HELPERS ---
#
# Custom answering (interaction) rules are NOT a User-only feature. Call Queues
# (Department), IVR menus, shared-line/paging groups and the announcement- and
# message-only extensions all carry their own answering rules. Auditing only
# `type=User` silently misses every one of those. The set below is the full
# list of extension types that can own an answering rule.
RULE_CAPABLE_TYPES = {
    'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited',
    'Department',          # Call Queue
    'IvrMenu',             # IVR menu / auto-receptionist
    'SharedLinesGroup',    # Shared lines group
    'PagingOnly',          # Paging group
    'Announcement', 'AnnouncementOnly',   # Announcement-only
    'Voicemail', 'MessageOnly',           # Message-only
    'ParkLocation',        # Park zone / location
}

# Human-readable label shown in the audit's Type column (and used to build the
# friendly filter list). Unknown types fall back to the raw API type string.
TYPE_LABELS = {
    'User': 'User',
    'DigitalUser': 'User',
    'VirtualUser': 'Virtual Extension',
    'FlexibleUser': 'User',
    'Limited': 'Limited Extension',
    'Department': 'Call Queue',
    'IvrMenu': 'IVR Menu',
    'SharedLinesGroup': 'Shared Lines Group',
    'PagingOnly': 'Paging Group',
    'Announcement': 'Announcement Only',
    'AnnouncementOnly': 'Announcement Only',
    'Voicemail': 'Message Only',
    'MessageOnly': 'Message Only',
    'ParkLocation': 'Park Location',
}

# Friendly filter label -> the raw API types it covers. Lets the UI offer a
# short list of options (e.g. "Call Queue") that transparently expands to every
# underlying API type ("Department"). Order here drives the order in the UI.
FILTER_GROUPS = {
    'User': {'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited'},
    'Call Queue': {'Department'},
    'IVR Menu': {'IvrMenu'},
    'Shared Lines Group': {'SharedLinesGroup'},
    'Paging Group': {'PagingOnly'},
    'Announcement Only': {'Announcement', 'AnnouncementOnly'},
    'Message Only': {'Voicemail', 'MessageOnly'},
    'Park Location': {'ParkLocation'},
}


def fetch_all_extensions():
    """Fetches every account extension across all pages (session token).

    The audit previously requested a single page of 1000 with `type=User`, which
    both capped large accounts at 1000 records and excluded every non-User
    extension. This walks the full directory so nothing is missed."""
    extensions = []
    page = 1
    while True:
        resp = rc_api_call('/restapi/v1.0/account/~/extension',
                           params={'perPage': 1000, 'page': page})
        if not resp or 'records' not in resp:
            break
        extensions.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
    return extensions


def fetch_sites():
    """Fetches every Site on the account (all pages) as {'id', 'name'} dicts."""
    sites = []
    page = 1
    while True:
        resp = rc_api_call('/restapi/v1.0/account/~/sites',
                           params={'perPage': 1000, 'page': page})
        if not resp or 'records' not in resp:
            break
        for s in resp['records']:
            sites.append({'id': s.get('id'), 'name': s.get('name', '')})
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
    return sites


def extension_type_label(ext):
    """Human-readable extension type for the audit Type column."""
    raw = ext.get('type') or ''
    return TYPE_LABELS.get(raw, raw or 'Unknown')


def extension_site_name(ext):
    """Friendly Site name for an extension (defaults to the primary site)."""
    if ext.get('type') == 'Site':
        return ext.get('name', 'Main Site')
    return (ext.get('site') or {}).get('name') or 'Main Site'


def extension_site_id(ext):
    """Current Site id for an extension (defaults to the primary site)."""
    if ext.get('type') == 'Site':
        return ext.get('id')
    return (ext.get('site') or {}).get('id') or 'main-site'


def resolve_type_filter(filter_labels):
    """Maps a list of friendly filter labels to the set of raw API types.

    An empty/omitted selection means "everything that can own a rule". Unknown
    labels are passed through untouched so a raw API type still works."""
    if not filter_labels:
        return set(RULE_CAPABLE_TYPES)
    raw = set()
    for label in filter_labels:
        label = (label or '').strip()
        if not label:
            continue
        if label in FILTER_GROUPS:
            raw |= FILTER_GROUPS[label]
        else:
            raw.add(label)
    return raw or set(RULE_CAPABLE_TYPES)


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
    interaction_cond = {"type": "Interaction"}
    
    if "calledNumbers" in v1_payload and v1_payload["calledNumbers"]:
        interaction_cond["to"] = [{"phoneNumber": item['phoneNumber']} for item in v1_payload['calledNumbers']]
        
    if "callers" in v1_payload and v1_payload["callers"]:
        interaction_cond["from"] = [{"phoneNumber": item['callerId']} for item in v1_payload['callers']]
        
    # Only append the Interaction block if we actually have data, preventing empty array errors
    if "to" in interaction_cond or "from" in interaction_cond:
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
        'Type': extension_type_label(ext),
        'Site': extension_site_name(ext),
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
            # The action carries its target under `targets[].type`; there is no
            # reliable `terminatingTargetType` on the action itself, so derive
            # the type from the terminating target directly.
            targets = term_action.get('targets', [])
            main_target = next(
                (t for t in targets if str(t.get('type', '')).endswith('TerminatingTarget')),
                None
            )
            if main_target is None and targets:
                main_target = targets[0]
            target_type = main_target.get('type') if main_target else None

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
