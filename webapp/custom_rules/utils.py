import pandas as pd
import re
from datetime import datetime
from webapp.rc_api import rc_api_call

# --- 0. EXTENSION TYPE / SITE HELPERS ---
#
# Custom answering (interaction) rules are NOT a User-only feature — Call Queues
# (Department), Shared Lines Groups and Sites carry their own answering rules
# too, and auditing only `type=User` silently misses them. Types that cannot own
# a custom answering rule (IVR menus, paging groups, announcement-only,
# message-only and park locations) are deliberately excluded so the scan and the
# filter list only offer extensions that can actually have rules.
RULE_CAPABLE_TYPES = {
    'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited',
    'Department',          # Call Queue
    'SharedLinesGroup',    # Shared lines group
    'Site',                # Site (site-level call handling)
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
    'Site': 'Site',
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
    'Shared Lines Group': {'SharedLinesGroup'},
    'Site': {'Site'},
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

def _parse_clock(t):
    """Parse a single clock value into 'HH:MM', tolerant of how people actually
    type times in a sheet. All of these work: '9:00 AM', '9:00am', '9am',
    '12:00AM', '09:00', '17:00'. Returns None if it can't be parsed."""
    s = str(t).strip().upper()
    if not s:
        return None
    # Insert a space before AM/PM when omitted ('12:00AM' -> '12:00 AM') and
    # collapse any surrounding whitespace, since strptime's '%p' needs the space.
    s = re.sub(r'\s*([AP]\.?M\.?)$', r' \1', s).replace('.', '')
    s = re.sub(r'\s+', ' ', s).strip()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def parse_time_range(range_str):
    """Parses '8:00 AM - 5:00 PM' or multiple '8:00 AM - 12:00 PM, 1:00 PM - 5:00 PM'
    into API format. Tolerant of spacing/case ('12:00am-12:00am', '9am - 5pm') and
    of 24-hour input ('00:00-23:59')."""
    if pd.isna(range_str) or not str(range_str).strip(): return None
    ranges = []
    for part in str(range_str).split(','):
        if '-' not in part: continue
        start, end = part.split('-', 1)
        f, t = _parse_clock(start), _parse_clock(end)
        if f is not None and t is not None:
            ranges.append({"from": f, "to": t})
    return ranges if ranges else None

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

# On New Call Handling accounts the V2 interaction-rules write rejects a greeting
# of effectiveGreetingType "Default" (CMN-414: effectiveGreetingType "should be
# specified"). A terminating target must carry a concrete greeting, so "no
# override" is expressed as the account's standard default-voicemail preset —
# id 590080, the value every live rule on the account carries for its VoiceMail
# target (test1, forward-all-calls and work-hours all use it).
DEFAULT_VM_GREETING = {"effectiveGreetingType": "Preset", "preset": {"id": "590080"}}


def sanitize_v2_greeting(greeting):
    """Reduces a V2 greeting object (as returned by a rule GET) to the minimal,
    writable shape, so it can be re-sent on a PUT without read-only fields.

    Preserves a Custom clip (by id) or a Preset (by id); anything else — or a
    missing greeting — becomes an explicit Default. Returns None only for a
    falsy input so callers can distinguish "no info" from "Default"."""
    if not greeting:
        return None
    egt = greeting.get('effectiveGreetingType')
    if egt == 'Custom' and (greeting.get('custom') or {}).get('id'):
        return {"effectiveGreetingType": "Custom", "custom": {"id": greeting['custom']['id']}}
    if egt == 'Preset' and (greeting.get('preset') or {}).get('id'):
        return {"effectiveGreetingType": "Preset", "preset": {"id": greeting['preset']['id']}}
    return dict(DEFAULT_VM_GREETING)


def get_existing_v2_greeting(ext_id, rule_id):
    """Fetches the greeting currently on a V2 interaction rule so an update can
    preserve it instead of clobbering it. Returns a sanitized greeting object or
    None when there's no existing rule/greeting to read."""
    if not rule_id:
        return None
    try:
        url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules/{rule_id}"
        resp = rc_api_call(url, raise_error=True)
        for action in (resp or {}).get('dispatching', {}).get('actions', []):
            for t in action.get('targets', []):
                g = (t.get('prompt') or {}).get('greeting')
                if g:
                    return sanitize_v2_greeting(g)
    except Exception:
        pass
    return None


def get_existing_v1_conditions(ext_id, rule_id):
    """Fetches an existing rule's match conditions (callers / called numbers /
    schedule) from the V1 answering-rule endpoint, so an update that only
    changes the action or greeting can carry the original conditions forward
    instead of blanking them. Returns a dict with only the keys that are set."""
    if not rule_id:
        return {}
    try:
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}',
            params={'view': 'Detailed'}, raise_error=True)
        out = {}
        if resp.get('callers'): out['callers'] = resp['callers']
        if resp.get('calledNumbers'): out['calledNumbers'] = resp['calledNumbers']
        if resp.get('schedule'): out['schedule'] = resp['schedule']
        return out
    except Exception:
        return {}


def get_existing_v2_conditions(ext_id, rule_id):
    """Fetches an existing V2 interaction rule's conditions so a V2 update can
    preserve them rather than submitting an empty conditions array."""
    if not rule_id:
        return []
    try:
        url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules/{rule_id}"
        resp = rc_api_call(url, raise_error=True)
        return (resp or {}).get('conditions', []) or []
    except Exception:
        return []


def fetch_v2_interaction_rules(ext_id):
    """Lists an extension's V2 custom interaction rules with FULL detail.

    The collection endpoint returns each rule with its dispatching as a
    `dispatchingRef` reference rather than the inline `dispatching.actions`, so
    parsing the list records directly leaves the Action (and every column
    derived from it — External Number, Transfer Extension, Voicemail Recipient,
    Greeting) blank while the rule still appears in the audit. The per-rule GET
    is what returns the authoritative body (the same inline `dispatching` /
    `conditions` shape the update path reads), so fetch each rule by id and only
    fall back to the summary record if that detail GET fails.

    Raises on a genuine V2 failure (e.g. a non-new-call-handling account where
    the endpoint 404s) so the caller can fall back to the V1 answering-rule
    endpoint; returns an empty list when the account is V2 but the extension has
    no custom rules (which must NOT suppress the V1 fallback)."""
    base = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
    records = []
    page = 1
    while True:
        resp = rc_api_call(base, params={'perPage': 100, 'page': page}, raise_error=True)
        page_records = (resp or {}).get('records') or []
        records.extend(page_records)
        paging = (resp or {}).get('paging') or {}
        if not page_records or page >= (paging.get('totalPages') or page):
            break
        page += 1
    detailed = []
    for rec in records:
        rid = rec.get('id')
        if not rid:
            detailed.append(rec)
            continue
        try:
            full = rc_api_call(f"{base}/{rid}", raise_error=True)
            detailed.append(full or rec)
        except Exception:
            detailed.append(rec)
    return detailed


# Weekday keys as they appear in a V1 `weeklyRanges` block.
_WEEKDAY_KEYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def _is_full_day_range(rng):
    """A single time range that spans a whole day. RingCentral stores end-of-day
    as 23:59, but a range entered as 00:00-00:00 (midnight to midnight) is also
    treated as all-day by the classic API, so accept both."""
    return rng.get('from') == '00:00' and rng.get('to') in ('23:59', '00:00')


def _hhmm_to_hms(hhmm, is_end=False):
    """'09:00' -> '09:00:00'. End-of-day '23:59' becomes '23:59:59' so a full day
    is covered with no gap."""
    hhmm = str(hhmm)
    if is_end and hhmm == '23:59':
        return '23:59:59'
    return f'{hhmm}:00'


def _schedule_v1_to_v2(schedule):
    """Convert a V1 answering-rule schedule (weeklyRanges/ranges with HH:MM
    from/to) into the V2 comm-handling schedule that New Call Handling interaction
    rules require — the V1 answering-rule API is disabled on those accounts, so
    the V2 interaction-rules write is the only path and it rejects the legacy
    weeklyRanges shape.

    An interaction-rule Schedule condition supports Daily, Weekly and Range
    triggers. A captured admin-UI create (HAR) proves the all-day case is a
    single Daily trigger, not a weekday-keyed Weekly object:

      - weeklyRanges that are the SAME single window every day -> one Daily
        trigger {triggerType, startTime, endTime}. All-day (00:00-23:59 or
        00:00-00:00 on every weekday) is one Daily 00:00:00-23:59:59 — the exact
        shape the UI sends and the API accepts.
      - weeklyRanges that differ by weekday -> a Weekly trigger whose `ranges`
        object is keyed by weekday, each entry {startTime, endTime} in HH:MM:SS.
      - ranges (specific dates) -> a Range trigger with startDateTime/endDateTime.

    Falls back to the original schedule if there's nothing convertible."""
    triggers = []

    weekly = schedule.get('weeklyRanges') or {}
    if weekly:
        wk = {}
        for day, ranges in weekly.items():
            out = []
            for r in (ranges or []):
                frm, to = r.get('from'), r.get('to')
                if frm is None or to is None:
                    continue
                if _is_full_day_range(r):
                    out.append({"startTime": "00:00:00", "endTime": "23:59:59"})
                else:
                    out.append({"startTime": _hhmm_to_hms(frm),
                                "endTime": _hhmm_to_hms(to, is_end=True)})
            if out:
                wk[day] = out
        if wk:
            # If every weekday carries the identical single window, this is a
            # uniform daily schedule (the all-day 24/7 case included) — collapse
            # it to one Daily trigger, which is what the admin UI sends and the
            # API accepts. Only fall back to Weekly when days genuinely differ.
            windows = list(wk.values())
            uniform = (
                len(wk) == len(_WEEKDAY_KEYS)
                and all(len(w) == 1 for w in windows)
                and len({(w[0]['startTime'], w[0]['endTime']) for w in windows}) == 1
            )
            if uniform:
                win = windows[0][0]
                triggers.append({"triggerType": "Daily",
                                 "startTime": win['startTime'],
                                 "endTime": win['endTime']})
            else:
                triggers.append({"triggerType": "Weekly", "ranges": wk})

    for r in (schedule.get('ranges') or []):
        frm, to = r.get('from'), r.get('to')
        if not frm or not to:
            continue
        # V1 date ranges are ISO 8601 with a trailing Z / millis; the V2 Range
        # trigger wants a plain local date-time (no zone marker, no fraction).
        triggers.append({"triggerType": "Range", "ranges": [
            {"startDateTime": str(frm).replace('Z', '').split('.')[0],
             "endDateTime": str(to).replace('Z', '').split('.')[0]}
        ]})

    if triggers:
        return {"triggers": triggers}
    return schedule


def _ring_group_actions(user_devices):
    """The mobile / desktop (+ per-device) ring groups a user interaction rule
    must carry. RingCentral rejects a rule with no AllMobileRingTarget
    (CHF-211 "One target with the type AllMobileRingTarget is allowed"), so the
    official create sample always includes them. Kept DISABLED here: the call is
    forwarded/terminated immediately rather than ringing the user's own devices
    first, matching an unconditional forward / transfer."""
    actions = [
        {"type": "RingGroupAction", "enabled": False,
         "targets": [{"type": "AllMobileRingTarget", "name": "My mobile apps"}], "duration": 30},
        {"type": "RingGroupAction", "enabled": False,
         "targets": [{"type": "AllDesktopRingTarget", "name": "My desktop"}], "duration": 30},
    ]
    for dev in (user_devices or []):
        actions.append({"type": "RingGroupAction", "enabled": False,
                        "targets": [{"type": "DeviceRingTarget", "device": {"id": dev['id']}}],
                        "duration": 30})
    return actions


def _vm_fallback_target(vm_prompt):
    """The VoiceMailTerminatingTarget that every TerminatingAction must carry on
    New Call Handling accounts. A rule without it is rejected with 'Must have
    Terminating Action with VoiceMailTerminatingTarget'. Shape mirrors a
    live-created rule (name + prompt, no mailbox/dispatchingType) so it acts as
    the ringing fallback while the real destination is the terminating target;
    with no mailbox it defaults to the owning extension's own voicemail."""
    return {"type": "VoiceMailTerminatingTarget", "name": "Voicemail", "prompt": vm_prompt}


def transform_v1_to_v2(v1_payload, owner_ext_id, user_devices=None, vm_greeting=None):
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
            "schedule": _schedule_v1_to_v2(v1_payload["schedule"])
        })

    # --- 3. Actions - Strict Schema ---
    v1_act = v1_payload.get("callHandlingAction")
    # Greeting for terminating targets. A preserved existing greeting (vm_greeting,
    # already sanitised to a writable Custom/Preset) wins so we don't clobber a
    # queue/user's custom voicemail. When there's none to preserve (a new rule),
    # fall back to the account default-voicemail preset rather than "Default",
    # which New Call Handling rejects on write (CMN-414).
    vm_prompt = {"greeting": vm_greeting or dict(DEFAULT_VM_GREETING)}

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
                "name": "Voicemail",
                "mailbox": {"id": owner_ext_id},
                "dispatchingType": "Terminating",
                "prompt": vm_prompt
            }],
            "terminatingTargetType": "VoiceMailTerminatingTarget"
        })
        v2["dispatching"]["actions"] = actions

    elif v1_act == "UnconditionalForwarding":
        dest_num = v1_payload.get("unconditionalForwarding", {}).get("phoneNumber")
        formatted_dest = format_phone(dest_num)
        v2["dispatching"]["actions"].extend(_ring_group_actions(user_devices))
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [
                _vm_fallback_target(vm_prompt),
                {
                    "type": "PhoneNumberTerminatingTarget",
                    "name": "__EXTERNAL_NUMBER__",
                    "destination": {"phoneNumber": formatted_dest},
                    "dispatchingType": "Terminating"
                }
            ],
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "terminatingTargetType": "PhoneNumberTerminatingTarget"
        })

    elif v1_act == "TransferToExtension":
        target_ext_id = v1_payload.get("transfer", {}).get("extension", {}).get("id")
        v2["dispatching"]["actions"].extend(_ring_group_actions(user_devices))
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [
                _vm_fallback_target(vm_prompt),
                {
                    "type": "ExtensionTerminatingTarget",
                    "name": "Extension",
                    "extension": {"id": target_ext_id},
                    "dispatchingType": "Terminating"
                }
            ],
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "terminatingTargetType": "ExtensionTerminatingTarget"
        })

    elif v1_act == "TakeMessagesOnly":
        vm_recipient_id = v1_payload.get("voicemail", {}).get("recipient", {}).get("id")
        v2["dispatching"]["actions"].extend(_ring_group_actions(user_devices))
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [{
                "type": "VoiceMailTerminatingTarget",
                "name": "Voicemail",
                "mailbox": {"id": vm_recipient_id},
                "prompt": vm_prompt,
                "dispatchingType": "Terminating"
            }],
            "terminatingTargetType": "VoiceMailTerminatingTarget"
        })

    elif v1_act == "PlayAnnouncementOnly":
        v2["dispatching"]["actions"].extend(_ring_group_actions(user_devices))
        v2["dispatching"]["actions"].append({
            "type": "TerminatingAction",
            "targets": [
                _vm_fallback_target(vm_prompt),
                {
                    "type": "PlayAnnouncementTerminatingTarget",
                    "name": "PlayAnnouncement",
                    "prompt": vm_prompt,
                    "dispatchingType": "Terminating"
                }
            ],
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "terminatingTargetType": "PlayAnnouncementTerminatingTarget"
        })

    return v2

# --- 4. AUDIT PARSER ---

def _resolve_ext_display(ext_obj, ext_lookup):
    """Turns a rule's target extension object into a readable extension number.

    Answering-rule transfer/voicemail targets usually carry only an internal
    `id` (and sometimes a `uri`) — not the extension number — so the audit
    columns came back blank. Resolve the id against the account directory to
    recover the extension number. We deliberately return the bare number (no
    name) so the audit round-trips cleanly back through the Edit upload, whose
    parser looks the value up by extension number. Falls back to the raw id if
    the target isn't in the directory (e.g. an external/cross-account id)."""
    if not ext_obj:
        return ''
    num = ext_obj.get('extensionNumber')
    if num:
        return str(num)
    eid = ext_obj.get('id')
    if eid is not None and ext_lookup:
        rec = ext_lookup.get(str(eid))
        if rec and rec.get('extensionNumber'):
            return str(rec.get('extensionNumber'))
    return str(eid) if eid is not None else ''


# Keyword the audit emits (and the update accepts) for a voicemail rule that
# deposits into the owning extension's own mailbox, matching RingCentral's
# "This extension" option.
THIS_EXTENSION = 'This extension'


def _voicemail_recipient_cell(target_obj, owner_ext, ext_lookup):
    """Audit value for a voicemail recipient. When the mailbox is the rule's own
    extension, emit 'This extension' (round-trips to the same on upload);
    otherwise emit the recipient's extension number."""
    if not target_obj:
        return ''
    tid = target_obj.get('id')
    if tid is not None and owner_ext is not None and str(tid) == str(owner_ext.get('id')):
        return THIS_EXTENSION
    return _resolve_ext_display(target_obj, ext_lookup)


def _greeting_cell_v2(target):
    """Greeting column value for a V2 terminating target."""
    g = (target.get('prompt') or {}).get('greeting') or {}
    egt = g.get('effectiveGreetingType')
    if egt in ('Custom', 'Preset'):
        return 'Custom'
    if egt == 'Default':
        return 'Default'
    return ''


def _greeting_cell_v1(rule, slot):
    """Greeting column value for a V1 rule's greeting slot (e.g. 'Voicemail').

    A custom or non-default preset greeting reads as 'Custom' (preserved on
    round-trip); an absent slot means RingCentral is using the Default."""
    for g in rule.get('greetings', []):
        if g.get('type') == slot:
            if g.get('custom'):
                return 'Custom'
            if (g.get('preset') or {}).get('id'):
                return 'Custom'
            return 'Default'
    return 'Default'


def _v2_phone_list(items):
    """Reads a V2 Interaction condition's from/to list to phone-number strings.

    RingCentral is inconsistent here: `from` comes back as objects
    ({'phoneNumber': '+61…'}) while `to` can come back as bare strings
    ('+61…'). Handle both (and skip blanks) so a called-number condition doesn't
    crash the whole audit — a single crashing rule otherwise takes out every
    rule on the extension because the audit loop shares one try/except."""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            val = it.get('phoneNumber') or it.get('callerId') or it.get('name')
        else:
            val = it
        if val:
            out.append(str(val))
    return out


def parse_rule_to_row(ext, rule, is_v2=False, ext_lookup=None):
    """Converts a RingCentral Rule (V1 or V2) into a flat Excel row.

    `ext_lookup` maps an extension id (str) -> the extension record, so transfer
    and voicemail targets that only expose an internal id can be shown as a
    readable extension number."""
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
        'External Number': '', 'Transfer Extension': '', 'Voicemail Recipient': '',
        'Greeting': '', 'Greeting File': ''
    }

    schedule_data = None

    if is_v2:
        for cond in rule.get('conditions', []):
            if cond.get('type') == 'Interaction':
                if 'from' in cond:
                    row['Caller ID'] = ', '.join(_v2_phone_list(cond['from']))
                if 'to' in cond:
                    row['Called Number'] = ', '.join(_v2_phone_list(cond['to']))
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
        # Prefer the inline dispatching (present on a per-rule GET); tolerate a
        # `dispatchingRef` if a summary record ever reaches here.
        dispatching = rule.get('dispatching') or rule.get('dispatchingRef', {}).get('dispatching') or {}
        actions = dispatching.get('actions', [])
        term_action = next((a for a in actions if a.get('type') == 'TerminatingAction'), None)
        
        if term_action:
            # A TerminatingAction carries a MENU of possible targets (voicemail,
            # announcement, external number, extension, …) and names the one
            # actually in effect via `terminatingTargetType`. Honour that field;
            # picking the first TerminatingTarget instead lands on the voicemail
            # entry (which RingCentral lists first) and mislabels every forward /
            # announcement rule as "Send to Voicemail".
            targets = term_action.get('targets', [])
            eff_type = term_action.get('terminatingTargetType')
            main_target = None
            if eff_type:
                # Several targets can share the effective type (e.g. a ringing-
                # phase announcement and the terminating one); prefer the one
                # whose dispatchingType is Terminating, else the first match.
                main_target = next(
                    (t for t in targets if t.get('type') == eff_type
                     and t.get('dispatchingType') == 'Terminating'), None)
                if main_target is None:
                    main_target = next((t for t in targets if t.get('type') == eff_type), None)
            if main_target is None:
                main_target = next(
                    (t for t in targets if str(t.get('type', '')).endswith('TerminatingTarget')),
                    None)
            if main_target is None and targets:
                main_target = targets[0]
            target_type = main_target.get('type') if main_target else None

            if target_type == 'PhoneNumberTerminatingTarget':
                row['Action'] = 'Transfer to External'
                if main_target: row['External Number'] = main_target.get('destination', {}).get('phoneNumber')
            elif target_type == 'ExtensionTerminatingTarget':
                row['Action'] = 'Transfer to Extension'
                if main_target: row['Transfer Extension'] = _resolve_ext_display(main_target.get('extension', {}), ext_lookup)
            elif target_type == 'VoiceMailTerminatingTarget':
                row['Action'] = 'Send to Voicemail'
                if main_target:
                    row['Voicemail Recipient'] = _voicemail_recipient_cell(main_target.get('mailbox', {}), ext, ext_lookup)
                    row['Greeting'] = _greeting_cell_v2(main_target)
            elif target_type == 'PlayAnnouncementTerminatingTarget':
                row['Action'] = 'Play Message'
                if main_target:
                    row['Greeting'] = _greeting_cell_v2(main_target)
    else:
        action_type = rule.get('callHandlingAction')
        if action_type == 'UnconditionalForwarding':
            row['Action'] = 'Transfer to External'
            row['External Number'] = rule.get('unconditionalForwarding', {}).get('phoneNumber')
        elif action_type == 'TransferToExtension':
            row['Action'] = 'Transfer to Extension'
            row['Transfer Extension'] = _resolve_ext_display(rule.get('transfer', {}).get('extension', {}), ext_lookup)
        elif action_type == 'TakeMessagesOnly':
            row['Action'] = 'Send to Voicemail'
            row['Voicemail Recipient'] = _voicemail_recipient_cell(rule.get('voicemail', {}).get('recipient', {}), ext, ext_lookup)
            row['Greeting'] = _greeting_cell_v1(rule, 'Voicemail')
        elif action_type == 'PlayAnnouncementOnly':
            row['Action'] = 'Play Message'
            row['Greeting'] = _greeting_cell_v1(rule, 'Announcement')

    return row
