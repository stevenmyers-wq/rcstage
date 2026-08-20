import io
import os
import time
import requests
import base64
import pandas as pd
from datetime import datetime
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.rc_api import rc_api_call
from webapp.auth_utils import get_impersonation_token
from webapp import task_control

# Global store for background task progress
audit_progress_store = {}

# The APPLY (upload) run mirrors the audit: it runs in a background thread and
# the browser polls for progress + log lines, so a bulk toggle apply can outlast
# the Cloud Run per-request timeout and account-lock waits happen server-side.
# Keys: status, current, total, message, logs (list of {level, message}),
# written, cancelled, error.
apply_progress_store = {}

# Physical endpoint types the audit and apply operate on. Softphones (desktop /
# mobile apps) are governed by their own app-ring settings, not a device toggle.
RING_DEVICE_TYPES = ['HardPhone', 'OtherPhone', 'Paging']

# Columns for the audit export AND the accepted upload layout. One row per rule x
# physical device. Editing this export in place and re-uploading is the ONLY
# supported input (there is no blank free-hand template): the operator must audit
# first so every row carries the Extension ID / Rule ID / Device ID / Schema keys
# the apply path needs to write the toggle back to the exact rule it came from.
AUDIT_COLS = [
    'Username', 'Extension', 'Extension ID',
    'Rule Name', 'Rule ID', 'Schema',
    'Device Name', 'Device ID', 'Device Type',
    'Ring Enabled',
    'Mobile App Ring Enabled', 'Desktop App Ring Enabled', 'External Numbers Ringing',
]

# The one editable column: an operator flips a device's ring toggle by choosing
# Yes / No. Blank means "leave this device unchanged".
RING_TOGGLE_COLUMN = 'Ring Enabled'
RING_TOGGLE_FORMULA = '"Yes,No"'

# The blank, free-hand template. Deliberately minimal: an operator types an
# extension number and a Yes/No, and (optionally) a Device ID copied from an
# audit. A blank Device ID means "all physical devices on the extension"; a
# blank Rule ID (there is no Rule column here) means "the default business-hours
# rule". The apply path accepts THIS sheet and a full audit export via the same
# reader, so the two share the editable 'Ring Enabled' column + dropdown.
TEMPLATE_COLS = ['Extension', 'Device ID', 'Device Name', 'Ring Enabled']

# V2 comm-handling endpoints. The audit reads ring state from BOTH the scheduled
# state-rules (business/after hours) and the custom interaction-rules, so the
# apply must look a Rule ID up in both lists to know which endpoint to PATCH.
V2_STATE_RULE_BASE = "/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"
V2_INTERACTION_RULE_BASE = "/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"

def safe_api_call(endpoint, method='GET', auth_data=None, task_id=None,
                  json_body=None, raw=False):
    """Helper to safely request API data while respecting 429 Rate Limits and healing expired tokens.

    Set ``json_body`` to send a JSON payload (PATCH / PUT writes) and ``raw=True``
    to get the response object back instead of parsed JSON, so a write caller can
    tell a real failure from an empty-but-OK body."""
    for attempt in range(4):
        active_token = auth_data['access_token'] if auth_data else None
        resp = rc_api_call(endpoint, method=method, json=json_body,
                           return_response=True, token=active_token)
        status_code = getattr(resp, 'status_code', None)
        
        # 1. Handle Rate Limiting (429)
        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 60
            if task_id:
                audit_progress_store[task_id]['message'] = f"API rate limit reached. Pausing for {retry_after} seconds..."
            time.sleep(retry_after + 1)
            continue
            
        # 2. Handle Token Expiry (401) with Background Self-Healing
        if status_code == 401:
            if auth_data:
                if task_id:
                    audit_progress_store[task_id]['message'] = "Access token expired. Executing background refresh..."
                
                # A. Heal Partner Impersonation Token
                if auth_data.get('sm_employee_token') and auth_data.get('sm_target_id'):
                    
                    # 1. Refresh the underlying Employee Token first
                    if auth_data.get('sm_employee_refresh_token'):
                        client_id = os.getenv('SM_CLIENT_ID')
                        client_secret = os.getenv('SM_CLIENT_SECRET')
                        token_url = f"{auth_data.get('server_url', 'https://platform.ringcentral.com')}/restapi/oauth/token"
                        
                        data = {
                            'grant_type': 'refresh_token',
                            'refresh_token': auth_data['sm_employee_refresh_token']
                        }
                        headers = { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' }
                        if client_secret:
                            auth_str = f"{client_id}:{client_secret}"
                            headers['Authorization'] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
                        else:
                            data['client_id'] = client_id
                            
                        try:
                            refresh_resp = requests.post(token_url, data=data, headers=headers)
                            if refresh_resp.ok:
                                new_tokens = refresh_resp.json()
                                auth_data['sm_employee_token'] = new_tokens.get('access_token')
                                auth_data['sm_employee_refresh_token'] = new_tokens.get('refresh_token')
                            else:
                                raise Exception(f"Employee token refresh rejected: {refresh_resp.text}")
                        except Exception as e:
                            raise Exception(f"Failed to refresh employee token: {str(e)}")
                            
                    # 2. Generate a new target customer token using the fresh employee token
                    new_token = get_impersonation_token(auth_data['sm_employee_token'], auth_data['sm_target_id'])
                    if new_token:
                        auth_data['access_token'] = new_token
                        if task_id:
                            audit_progress_store[task_id]['message'] = "Impersonation token refreshed! Resuming audit..."
                        time.sleep(1)
                        continue
                
                # B. Heal Standard OAuth Token
                elif auth_data.get('refresh_token') and auth_data.get('client_id'):
                    token_url = f"{auth_data.get('server_url', 'https://platform.ringcentral.com')}/restapi/oauth/token"
                    payload = {
                        'grant_type': 'refresh_token',
                        'refresh_token': auth_data['refresh_token'],
                        'client_id': auth_data['client_id']
                    }
                    try:
                        refresh_resp = requests.post(token_url, data=payload, headers={'Content-Type': 'application/x-www-form-urlencoded'})
                        if refresh_resp.ok:
                            new_tokens = refresh_resp.json()
                            auth_data['access_token'] = new_tokens.get('access_token')
                            auth_data['refresh_token'] = new_tokens.get('refresh_token')
                            if task_id:
                                audit_progress_store[task_id]['message'] = "OAuth token refreshed! Resuming audit..."
                            time.sleep(1)
                            continue
                        else:
                            raise Exception(f"OAuth token refresh rejected: {refresh_resp.text}")
                    except Exception as e:
                        raise Exception(f"Failed to refresh OAuth token: {str(e)}")
                        
            raise Exception("Authentication token expired during audit and could not be healed. Please Authorize & Bridge again.")
            
        # 3. Handle Success
        if resp and getattr(resp, 'ok', False):
            if raw:
                return resp
            try:
                return resp.json()
            except:
                return {}

        # 4. Handle Server/Gateway Errors (50x)
        if status_code and status_code >= 500:
            time.sleep(3)
            continue

        # For 400 Bad Request, 403 Forbidden or 404 Not Found, don't retry.
        # Writers want the response object to read the status/body for the log.
        return resp if raw else None

    return None

def fetch_users_for_ui(auth_data):
    """Fetches all active users from the account for the UI table."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", auth_data=auth_data)
        if not resp or 'records' not in resp: 
            break
            
        for u in resp['records']:
            if u.get('status') in ['Enabled', 'NotActivated']:
                users.append({
                    'id': str(u['id']),
                    'name': u.get('name', 'Unknown'),
                    'extensionNumber': u.get('extensionNumber', ''),
                    'site': u.get('site', {}).get('name', 'Main Site')
                })
                
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return users

def fetch_all_devices(auth_data, task_id=None):
    """Fetches all devices from the account to avoid redundant per-user API calls."""
    devices = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/device?perPage=1000&page={page}", auth_data=auth_data, task_id=task_id)
        if not resp or 'records' not in resp: 
            break
        devices.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.1)
    return devices

def get_device_ringing_status(ext_id, user_devices, auth_data, task_id=None):
    """
    Evaluates ALL configured call handling rules.
    Queries V2 Schema first (Source of truth for migrated accounts), then falls back to V1.

    Returns (device_map, rules_data, schema) where schema is 'V2', 'V1' or 'Unknown'.
    Each rule in rules_data also carries 'rule_id' so the edited audit can be
    re-uploaded and the ring toggle applied back to the exact rule it came from.
    """
    device_map = {}
    for d in user_devices:
        d_type = d.get('type', 'Unknown')
        d_name = d.get('name') or d.get('model', {}).get('name', 'Unknown Device')

        # STRICT WHITELIST: Only process actual physical endpoints.
        if d_type not in ['HardPhone', 'OtherPhone', 'Paging']:
            continue

        device_map[str(d['id'])] = {
            'name': d_name,
            'type': d_type
        }

    rules_data = []
    schema = 'Unknown'

    # ==========================================
    # 1. V2 SCHEMA (PRIMARY)
    # ==========================================
    v2_state_rules_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"
    v2_state_resp = safe_api_call(v2_state_rules_url, auth_data=auth_data, task_id=task_id)
    
    is_v2 = False

    if v2_state_resp and 'records' in v2_state_resp:
        is_v2 = True
        schema = 'V2'
        v2_rules_to_check = []
        
        # Load State Rules (Business Hours, After Hours)
        for rule in v2_state_resp['records']:
            rule_id = rule.get('id', '')
            
            # FILTER: Ignore RingCentral's hidden system/toggle states so they don't duplicate/clutter the report
            if rule_id in ['agent', 'dnd', 'forward-all-calls']:
                continue
                
            if not rule.get('state', {}).get('enabled', True):
                continue
                
            v2_rules_to_check.append(rule)
            
        # Load Custom Interaction Rules
        v2_interaction_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
        v2_interaction_resp = safe_api_call(v2_interaction_url, auth_data=auth_data, task_id=task_id)
        if v2_interaction_resp and 'records' in v2_interaction_resp:
            for rule in v2_interaction_resp['records']:
                if not rule.get('enabled', True):
                    continue
                v2_rules_to_check.append(rule)

        # Parse all collected V2 rules
        for rule in v2_rules_to_check:
            r_name = rule.get('displayName') or rule.get('name') or rule.get('id') or 'Unnamed V2 Rule'
            r_id = str(rule.get('id') or '')
            mobile_en = False
            desktop_en = False
            external_nums = []
            dev_status = {dev_id: False for dev_id in device_map.keys()}
            
            dispatching = rule.get('dispatching') or rule.get('dispatchingRef', {}).get('dispatching') or {}
            actions = dispatching.get('actions', [])
            
            for action in actions:
                if action.get('enabled', True) == False:
                    continue
                    
                a_type = action.get('type', '')
                targets = action.get('targets', [])
                
                for t in targets:
                    if t.get('enabled', True) == False:
                        continue
                        
                    t_type = t.get('type', '')
                    
                    if a_type == 'RingGroupAction':
                        if t_type == 'AllMobileRingTarget':
                            mobile_en = True
                        elif t_type == 'AllDesktopRingTarget':
                            desktop_en = True
                        elif t_type == 'DeviceRingTarget':
                            did = str(t.get('device', {}).get('id', ''))
                            if did in dev_status:
                                dev_status[did] = True
                        elif t_type == 'PhoneNumberRingTarget':
                            p_num = str(t.get('phoneNumber', ''))
                            if p_num:
                                external_nums.append(p_num)
                                
                    elif a_type == 'TerminatingAction' and t_type == 'PhoneNumberTerminatingTarget':
                        p_num = str(t.get('destination', {}).get('phoneNumber', '')) or str(t.get('phoneNumber', ''))
                        if p_num:
                            external_nums.append(p_num)
                    
            rules_data.append({
                'rule_name': r_name,
                'rule_id': r_id,
                'mobile_enabled': mobile_en,
                'desktop_enabled': desktop_en,
                'external_numbers': external_nums,
                'device_status': dev_status
            })

    # ==========================================
    # 2. V1 SCHEMA (FALLBACK)
    # ==========================================
    if not is_v2:
        v1_rules_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
        v1_resp = safe_api_call(v1_rules_url, auth_data=auth_data, task_id=task_id)

        if v1_resp and 'records' in v1_resp:
            schema = 'V1'
            for rule_summary in v1_resp['records']:
                if rule_summary.get('enabled', False) and rule_summary.get('callHandlingAction') == 'ForwardCalls':
                    rule_id = rule_summary.get('id')

                    rule_detail_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}"
                    rule_detail = safe_api_call(rule_detail_url, auth_data=auth_data, task_id=task_id)

                    if rule_detail and 'forwarding' in rule_detail:
                        r_name = rule_detail.get('name', rule_summary.get('name', 'Unnamed V1 Rule'))
                        r_id = str(rule_detail.get('id') or rule_id or '')
                        mobile_en = False
                        desktop_en = False
                        external_nums = []
                        dev_status = {dev_id: False for dev_id in device_map.keys()}
                        
                        for r in rule_detail['forwarding'].get('rules', []):
                            if r.get('enabled', True) or r.get('active', True):
                                for f in r.get('forwardingNumbers', []):
                                    f_type = f.get('type', '')
                                    f_device_id = str(f.get('device', {}).get('id', ''))
                                    f_phone = str(f.get('phoneNumber', ''))
                                    
                                    if f_type in ['SoftPhone', 'ApplicationExtension']:
                                        desktop_en = True
                                        mobile_en = True
                                    elif f_device_id and f_device_id in dev_status:
                                        dev_status[f_device_id] = True
                                    elif f_phone and f_type not in ['SoftPhone', 'ApplicationExtension']:
                                        external_nums.append(f_phone)
                                        
                        rules_data.append({
                            'rule_name': r_name,
                            'rule_id': r_id,
                            'mobile_enabled': mobile_en,
                            'desktop_enabled': desktop_en,
                            'external_numbers': external_nums,
                            'device_status': dev_status
                        })
                    time.sleep(0.5)

    return device_map, rules_data, schema

def run_audit_background(task_id, auth_data, ext_ids=None):
    """Background task to perform the audit, updating progress as it goes."""
    try:
        valid_users = fetch_users_for_ui(auth_data)
        
        if ext_ids and len(ext_ids) > 0:
            valid_users = [u for u in valid_users if str(u['id']) in ext_ids]
            
        total_users = len(valid_users)
        audit_progress_store[task_id]['total'] = total_users
        
        if total_users == 0:
            audit_progress_store[task_id]['status'] = 'error'
            audit_progress_store[task_id]['error'] = 'No active users found to audit.'
            return

        # Optimization: Fetch all devices on the account at once to eliminate 1 API call per user
        audit_progress_store[task_id]['message'] = 'Fetching all account devices (Optimization)...'
        all_devices = fetch_all_devices(auth_data, task_id=task_id)
        
        devices_by_ext = {}
        for d in all_devices:
            d_ext_id = str(d.get('extension', {}).get('id', ''))
            if d_ext_id:
                if d_ext_id not in devices_by_ext:
                    devices_by_ext[d_ext_id] = []
                devices_by_ext[d_ext_id].append(d)

        audit_data = []

        for i, user in enumerate(valid_users):
            ext_id = str(user['id'])
            ext_name = user.get('name', 'Unknown')
            ext_num = user.get('extensionNumber', '')

            audit_progress_store[task_id]['current'] = i + 1
            audit_progress_store[task_id]['message'] = f"Auditing Extension {ext_num} ({ext_name})..."

            user_devices = devices_by_ext.get(ext_id, [])
            device_map, rules_data, schema = get_device_ringing_status(ext_id, user_devices, auth_data, task_id=task_id)

            if not rules_data:
                rules_data = [{
                    'rule_name': 'No Enabled Rules Found',
                    'rule_id': '',
                    'mobile_enabled': False,
                    'desktop_enabled': False,
                    'external_numbers': [],
                    'device_status': {did: False for did in device_map.keys()}
                }]

            # LONG (tidy) layout: one row per rule x physical device, so the export
            # can be edited in place (flip the 'Ring Enabled' cell) and re-uploaded
            # to apply the toggle back to the exact rule/device it came from. The
            # Extension ID / Rule ID / Device ID / Schema columns are the keys the
            # apply path reads; the Mobile/Desktop/External columns are informational.
            for r_data in rules_data:
                ext_apps = {
                    "Mobile App Ring Enabled": "Yes" if r_data['mobile_enabled'] else "No",
                    "Desktop App Ring Enabled": "Yes" if r_data['desktop_enabled'] else "No",
                    "External Numbers Ringing": ", ".join(r_data['external_numbers']) if r_data['external_numbers'] else "None",
                }

                if device_map:
                    for did, d_info in device_map.items():
                        is_ringing = r_data['device_status'].get(did, False)
                        row = {
                            "Username": ext_name,
                            "Extension": ext_num,
                            "Extension ID": ext_id,
                            "Rule Name": r_data['rule_name'],
                            "Rule ID": r_data.get('rule_id', ''),
                            "Schema": schema,
                            "Device Name": d_info['name'],
                            "Device ID": did,
                            "Device Type": d_info['type'],
                            "Ring Enabled": "Yes" if is_ringing else "No",
                        }
                        row.update(ext_apps)
                        audit_data.append(row)
                else:
                    # Rule with no physical device targets (rings apps / external
                    # numbers only). Kept for visibility; blank Device ID means the
                    # apply path skips it (nothing to toggle).
                    row = {
                        "Username": ext_name,
                        "Extension": ext_num,
                        "Extension ID": ext_id,
                        "Rule Name": r_data['rule_name'],
                        "Rule ID": r_data.get('rule_id', ''),
                        "Schema": schema,
                        "Device Name": "(no physical devices)",
                        "Device ID": "",
                        "Device Type": "",
                        "Ring Enabled": "",
                    }
                    row.update(ext_apps)
                    audit_data.append(row)

            # SUSTAINABLE PACING: Space out API calls to prevent rapid bucket depletion
            time.sleep(3.0)

        audit_progress_store[task_id]['message'] = "Compiling Excel Spreadsheet..."
        df = pd.DataFrame(audit_data)

        for c in AUDIT_COLS:
            if c not in df.columns:
                df[c] = ''
        df = df[AUDIT_COLS]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Device Ringing Audit')
            worksheet = writer.sheets['Device Ringing Audit']
            add_ring_toggle_dropdown(worksheet, AUDIT_COLS)
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

        audit_progress_store[task_id]['file_data'] = output.getvalue()
        audit_progress_store[task_id]['status'] = 'completed'
        audit_progress_store[task_id]['message'] = 'Audit successfully completed.'

    except Exception as e:
        audit_progress_store[task_id]['status'] = 'error'
        audit_progress_store[task_id]['error'] = str(e)


# ==========================================================================
# WORKBOOK HELPERS
# ==========================================================================

def add_ring_toggle_dropdown(ws, columns, max_row=5000):
    """Attaches a Yes/No dropdown to the editable 'Ring Enabled' column so an
    operator can only enter a value the apply path understands."""
    if RING_TOGGLE_COLUMN not in columns:
        return
    letter = get_column_letter(columns.index(RING_TOGGLE_COLUMN) + 1)
    dv = DataValidation(type="list", formula1=RING_TOGGLE_FORMULA, allow_blank=True)
    dv.error = 'Choose Yes or No (blank = leave this device unchanged).'
    dv.errorTitle = 'Invalid ring toggle'
    ws.add_data_validation(dv)
    dv.add(f"{letter}2:{letter}{max_row}")


# ==========================================================================
# APPLY: write the edited 'Ring Enabled' toggle back to RingCentral
# ==========================================================================

def coerce_toggle(value):
    """Reads a 'Ring Enabled' cell into True / False / None.

    True  -> the device should ring on this rule.
    False -> the device should NOT ring on this rule.
    None  -> blank / unrecognised: leave this device unchanged."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in ('yes', 'y', 'true', '1', 'on', 'enabled', 'enable', 'ring', 'ringing'):
        return True
    if raw in ('no', 'n', 'false', '0', 'off', 'disabled', 'disable', 'none'):
        return False
    return None


def _clean_id(value):
    """Normalises an id/number cell from the sheet (pandas can render an integer
    id as '12345.0')."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    s = str(value).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s


def _dispatching_of(rule):
    """Returns the dispatching dict of a V2 rule, tolerating the interaction-rule
    shape where it hangs off `dispatchingRef`. Returns the live object so callers
    mutate in place before PATCHing it back."""
    return rule.get('dispatching') or (rule.get('dispatchingRef') or {}).get('dispatching') or {}


def _device_ring_targets(dispatching, did):
    """Yields every DeviceRingTarget for `did` inside a RingGroupAction, paired
    with its owning action, so a caller can read or flip its enabled flag."""
    for action in dispatching.get('actions', []):
        if action.get('type') != 'RingGroupAction':
            continue
        for t in action.get('targets', []):
            if t.get('type') != 'DeviceRingTarget':
                continue
            if str((t.get('device') or {}).get('id') or '') == did:
                yield action, t


def _device_currently_rings(dispatching, did):
    """Mirrors the audit read: True when `did` has an enabled DeviceRingTarget in
    an enabled RingGroupAction."""
    for action, target in _device_ring_targets(dispatching, did):
        if action.get('enabled', True) is False:
            continue
        if target.get('enabled', True) is False:
            continue
        return True
    return False


def _set_v2_device_toggle(dispatching, did, want, device_info=None):
    """Flips one device's ring toggle inside a V2 rule's dispatching (in place).

    RingCentral models each phone as its OWN single-target RingGroupAction: the
    on/off toggle the admin portal shows is that ACTION's `enabled` flag, and the
    DeviceRingTarget itself carries no `enabled` field. So we toggle the action,
    not the target (an earlier version flipped a target-level flag RC ignores).

    Returns 'changed', 'nochange', or 'cannot' (enable requested but the device
    has no ring group and we can't synthesise one)."""
    if _device_currently_rings(dispatching, did) == want:
        return 'nochange'

    pairs = list(_device_ring_targets(dispatching, did))  # (action, target) for this device

    if want:
        if pairs:
            # Turn the device's ring group(s) back on.
            for action, target in pairs:
                action['enabled'] = True
                if target.get('enabled') is False:
                    target.pop('enabled', None)
            return 'changed'
        # Device isn't targeted by any ring group yet — synthesise one that mirrors
        # RingCentral's own shape (a single-device RingGroupAction) and slot it in
        # before the terminating action so voicemail still runs last.
        target = {'type': 'DeviceRingTarget', 'device': {'id': did}}
        if device_info:
            if device_info.get('name'):
                target['name'] = device_info['name']
                target['device']['name'] = device_info['name']
            if device_info.get('phoneNumber'):
                target['device']['phoneNumber'] = device_info['phoneNumber']
        new_action = {'type': 'RingGroupAction', 'enabled': True,
                      'duration': 20, 'targets': [target]}
        actions = dispatching.setdefault('actions', [])
        idx = next((i for i, a in enumerate(actions)
                    if a.get('type') == 'TerminatingAction'), len(actions))
        actions.insert(idx, new_action)
        return 'changed'

    # want == False: silence the device by disabling its ring group. A group can
    # in principle carry more than one device target; only when this device is the
    # ONLY one do we disable the whole action, otherwise we drop just this target.
    changed = False
    for action, target in pairs:
        if action.get('enabled', True) is False:
            continue
        other_device_targets = [
            t for t in action.get('targets', [])
            if t.get('type') == 'DeviceRingTarget'
            and str((t.get('device') or {}).get('id') or '') != did
        ]
        if other_device_targets:
            action['targets'] = [t for t in action.get('targets', []) if t is not target]
        else:
            action['enabled'] = False
        changed = True
    return 'changed' if changed else 'nochange'


def _apply_v2(ext_id, rule_id, device_wants, auth_data=None, task_id=None):
    """Applies the requested device toggles to one V2 rule (state OR interaction).

    Looks the rule up by id in the state-rules endpoint first, then the
    interaction-rules endpoint, mutates the ring targets, and PATCHes the
    modified dispatching back to whichever endpoint owned it (PATCH — a PUT
    returns AGW-404 on comm-handling). Returns (ok, changed_count, message)."""
    kind = None
    rule = None
    for k, base in (('state', V2_STATE_RULE_BASE), ('interaction', V2_INTERACTION_RULE_BASE)):
        resp = safe_api_call(base.format(ext_id=ext_id) + f"/{rule_id}",
                             auth_data=auth_data, task_id=task_id)
        if isinstance(resp, dict) and (resp.get('dispatching') or resp.get('dispatchingRef')):
            kind, rule = k, resp
            break
    if rule is None:
        return False, 0, f"rule {rule_id} not found on V2 comm-handling — skipped."

    dispatching = _dispatching_of(rule)

    # If enabling a device that has no ring group yet, we synthesise one and need
    # its name/number to mirror RC's shape — fetch device metadata once, only when
    # that case actually arises.
    device_info_map = {}
    if any(want and not list(_device_ring_targets(dispatching, did))
           for did, want in device_wants.items()):
        device_info_map = _fetch_device_info_map(ext_id, auth_data=auth_data, task_id=task_id)

    changed = 0
    notes = []
    for did, want in device_wants.items():
        result = _set_v2_device_toggle(dispatching, did, want,
                                       device_info=device_info_map.get(did))
        if result == 'changed':
            changed += 1
        elif result == 'cannot':
            notes.append(f"device {did} could not be enabled (rule has no ring group)")

    if changed == 0:
        base_msg = "already matched the sheet — nothing to change."
        if notes:
            base_msg = "; ".join(notes) + "."
        return True, 0, base_msg

    base = V2_STATE_RULE_BASE if kind == 'state' else V2_INTERACTION_RULE_BASE
    url = base.format(ext_id=ext_id) + f"/{rule_id}"
    resp = safe_api_call(url, method='PATCH', json_body={'dispatching': dispatching},
                         auth_data=auth_data, task_id=task_id, raw=True)
    if resp is None or not getattr(resp, 'ok', False):
        status = getattr(resp, 'status_code', '?')
        text = ((getattr(resp, 'text', '') or '').strip())[:300]
        return False, 0, f"V2 PATCH failed [{status}] {text or '(no body)'}"

    # VERIFY: a 2xx from comm-handling doesn't guarantee the edit stuck (RC can
    # accept then normalise/ignore an unsupported shape). Re-read the rule and
    # confirm each device now matches what was asked before reporting success.
    verify = safe_api_call(url, auth_data=auth_data, task_id=task_id)
    if not isinstance(verify, dict):
        note = (" (" + "; ".join(notes) + ")") if notes else ""
        return True, changed, f"updated {changed} device toggle(s) [{kind}]{note} — could not re-read to verify."
    vdisp = _dispatching_of(verify)
    mismatched = [did for did, want in device_wants.items()
                  if _device_currently_rings(vdisp, did) != want]
    if mismatched:
        return False, 0, (f"PATCH returned OK but {len(mismatched)} device(s) did not change "
                          f"server-side (RingCentral rejected or normalised the edit): "
                          f"{', '.join(mismatched)}.")
    note = (" (" + "; ".join(notes) + ")") if notes else ""
    return True, changed, f"updated {changed} device toggle(s) [{kind}]{note} — verified."


def _apply_v1(ext_id, rule_id, device_wants, auth_data=None, task_id=None):
    """Best-effort apply for legacy V1 answering rules: flips the `enabled` flag
    on each forwardingNumber that references a target device, then PUTs the rule
    back. Classic accounts don't always represent a user's own phones as
    forwarding numbers, so a device that isn't present is reported, not guessed."""
    url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}"
    rule = safe_api_call(url, auth_data=auth_data, task_id=task_id)
    if not isinstance(rule, dict) or 'forwarding' not in rule:
        return False, 0, f"V1 rule {rule_id} not found — skipped."

    changed = 0
    missing = []
    for did, want in device_wants.items():
        found = False
        for r in rule.get('forwarding', {}).get('rules', []):
            for fn in r.get('forwardingNumbers', []):
                if str((fn.get('device') or {}).get('id') or '') == did:
                    found = True
                    if fn.get('enabled', True) != want:
                        fn['enabled'] = want
                        changed += 1
        if not found:
            missing.append(did)

    if changed == 0:
        if missing:
            return True, 0, f"device(s) {', '.join(missing)} not in V1 forwarding config — left unchanged."
        return True, 0, "already matched the sheet — nothing to change."

    body = dict(rule)
    body.pop('uri', None)
    resp = safe_api_call(url, method='PUT', json_body=body,
                         auth_data=auth_data, task_id=task_id, raw=True)
    if resp is not None and getattr(resp, 'ok', False):
        note = f" ({len(missing)} device(s) not present, skipped)" if missing else ""
        return True, changed, f"updated {changed} device toggle(s) [V1 best-effort]{note}."
    status = getattr(resp, 'status_code', '?')
    text = ((getattr(resp, 'text', '') or '').strip())[:300]
    return False, 0, f"V1 PUT failed [{status}] {text or '(no body)'}"


def apply_device_toggles(ext_id, schema, rule_id, device_wants, auth_data=None, task_id=None):
    """Dispatches a single rule's device toggles to the right schema handler.
    Returns (ok, changed_count, message)."""
    if not rule_id:
        return False, 0, "no rule to apply to (could not resolve a default call-handling rule)."
    if str(schema).upper().startswith('V1'):
        return _apply_v1(ext_id, rule_id, device_wants, auth_data=auth_data, task_id=task_id)
    return _apply_v2(ext_id, rule_id, device_wants, auth_data=auth_data, task_id=task_id)


# --- Resolvers used by the free-hand template path -------------------------
# A blank template row identifies work loosely (an extension NUMBER, no Rule ID,
# maybe no Device ID). These resolve that into the concrete ids the apply needs.

def get_extension_id(ext_num, auth_data=None, task_id=None):
    """Resolves an extension NUMBER to its internal id (for template rows that
    carry no 'Extension ID' column)."""
    ext_num = str(ext_num).strip()
    if ext_num.endswith('.0'):
        ext_num = ext_num[:-2]
    if not ext_num:
        return None
    data = safe_api_call(f"/restapi/v1.0/account/~/extension?extensionNumber={ext_num}",
                         auth_data=auth_data, task_id=task_id)
    if isinstance(data, dict) and data.get('records'):
        return str(data['records'][0].get('id') or '') or None
    return None


def fetch_physical_devices(ext_id, auth_data=None, task_id=None):
    """Returns the ids of an extension's physical endpoints (deskphone / generic
    SIP / paging) — the set a blank Device ID expands to."""
    data = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device",
                         auth_data=auth_data, task_id=task_id)
    recs = data.get('records', []) if isinstance(data, dict) else []
    return [str(d.get('id')) for d in recs
            if d.get('type') in RING_DEVICE_TYPES and d.get('id')]


def _fetch_device_info_map(ext_id, auth_data=None, task_id=None):
    """Maps device id -> {name, phoneNumber} for an extension, used to build a
    faithful DeviceRingTarget when enabling a phone that has no ring group yet."""
    data = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device",
                         auth_data=auth_data, task_id=task_id)
    recs = data.get('records', []) if isinstance(data, dict) else []
    out = {}
    for d in recs:
        did = str(d.get('id') or '')
        if not did:
            continue
        phone = ''
        for pl in (d.get('phoneLines') or []):
            phone = (pl.get('phoneInfo') or {}).get('phoneNumber') or phone
        out[did] = {
            'name': d.get('name') or (d.get('model') or {}).get('name') or 'Device',
            'phoneNumber': phone,
        }
    return out


def resolve_default_rule(ext_id, auth_data=None, task_id=None):
    """Finds the extension's DEFAULT (business-hours) call-handling rule for a
    template row that names no specific rule. Returns (schema, rule_id, kind):
    the enabled, non-system V2 state rule that reads like business/work hours,
    else the legacy V1 'business-hours-rule'."""
    v2 = safe_api_call(V2_STATE_RULE_BASE.format(ext_id=ext_id),
                       auth_data=auth_data, task_id=task_id)
    if isinstance(v2, dict) and 'records' in v2:
        candidates = []
        for r in v2['records']:
            if str(r.get('id') or '') in ('agent', 'dnd', 'forward-all-calls'):
                continue
            if not (r.get('state') or {}).get('enabled', True):
                continue
            candidates.append(r)
        if candidates:
            def score(r):
                label = (str(r.get('displayName') or '') + ' ' +
                         str((r.get('state') or {}).get('displayName') or '') + ' ' +
                         str(r.get('id') or '')).lower()
                if 'business' in label or 'work' in label:
                    return 0
                if any(k in label for k in ('after', 'night', 'holiday', 'closed')):
                    return 2
                return 1
            candidates.sort(key=score)
            return 'V2', str(candidates[0].get('id') or ''), 'state'
        return 'V2', '', 'state'
    return 'V1', 'business-hours-rule', 'v1'


def parse_apply_upload(df):
    """Reads BOTH accepted inputs — a full audit export and the blank free-hand
    template — into work grouped by extension then rule:

        { ext_key: {'ext_id': '', 'ext_num': '101', 'label': 'Ext 101 (Jane)',
                    'rules': { rule_key: {'schema': 'V2',
                                          'devices': { device_key: want_bool }}}}}

    Where a value is absent the apply path resolves it at run time:
      - no Extension ID  -> resolve the Extension NUMBER to an id,
      - no Rule ID (rule_key '')     -> the extension's default business-hours rule,
      - no Device ID (device_key '') -> every physical device on the extension.

    A row is skipped only when 'Ring Enabled' is blank, or it names no extension
    at all. Returns (groups, considered_rows)."""
    groups = {}
    considered = 0
    for _, row in df.iterrows():
        want = coerce_toggle(row.get('Ring Enabled'))
        if want is None:
            continue
        ext_id = _clean_id(row.get('Extension ID'))
        ext_num = _clean_id(row.get('Extension'))
        if not ext_id and not ext_num:
            continue
        rule_id = _clean_id(row.get('Rule ID'))    # '' -> default rule
        did = _clean_id(row.get('Device ID'))       # '' -> all physical devices
        considered += 1

        ext_key = ext_id or f"num:{ext_num}"
        g = groups.setdefault(ext_key, {
            'ext_id': ext_id,
            'ext_num': ext_num,
            'label': f"Ext {ext_num or ext_id} ({str(row.get('Username') or '').strip() or '—'})",
            'rules': {},
        })
        if not g['ext_num'] and ext_num:
            g['ext_num'] = ext_num

        rb = g['rules'].setdefault(rule_id, {'schema': '', 'devices': {}})
        if not rb['schema'] and row.get('Schema') is not None and str(row.get('Schema')).strip():
            rb['schema'] = str(row.get('Schema')).strip()
        rb['devices'][did] = want
    return groups, considered


def _apply_log(store, level, message):
    store['logs'].append({'level': level, 'message': message})


def run_apply_background(task_id, groups, auth_data):
    """Applies every edited toggle, one rule at a time, recording per-rule log
    lines and progress into apply_progress_store[task_id] for the browser to
    poll. Honours a cooperative stop via task_control."""
    store = apply_progress_store.get(task_id)
    if store is None:
        return
    try:
        total = sum(len(g['rules']) for g in groups.values())
        store['total'] = total
        written = 0
        current = 0
        for ext_key, g in groups.items():
            if store.get('cancelled'):
                break

            # Resolve the extension id up front (template rows carry only a number).
            ext_id = g['ext_id']
            if not ext_id and g['ext_num']:
                ext_id = get_extension_id(g['ext_num'], auth_data=auth_data, task_id=task_id)
            if not ext_id:
                current += len(g['rules'])
                store['current'] = current
                _apply_log(store, 'error', f"❌ {g['label']}: extension not found.")
                continue

            for rule_key, rb in g['rules'].items():
                if task_control.is_stopped(task_id):
                    _apply_log(store, 'info', '■ Stopped by user — remaining rules were skipped.')
                    store['cancelled'] = True
                    break
                current += 1
                store['current'] = current
                store['message'] = f"Applying {g['label']} ({current}/{total})…"
                try:
                    schema = rb['schema']
                    rule_id = rule_key
                    # No Rule ID (blank template) -> target the default rule.
                    if not rule_id:
                        schema, rule_id, _kind = resolve_default_rule(
                            ext_id, auth_data=auth_data, task_id=task_id)
                        if not rule_id:
                            _apply_log(store, 'error',
                                       f"❌ {g['label']}: no default call-handling rule found.")
                            continue

                    # Split explicit device targets from the all-devices request
                    # (a blank Device ID). The all-devices toggle expands to every
                    # physical endpoint on the extension.
                    device_wants = {}
                    all_toggle = None
                    for did, want in rb['devices'].items():
                        if did == '':
                            all_toggle = want
                        else:
                            device_wants[did] = want
                    if all_toggle is not None:
                        phys = fetch_physical_devices(ext_id, auth_data=auth_data, task_id=task_id)
                        if not phys and not device_wants:
                            _apply_log(store, 'error',
                                       f"❌ {g['label']}: no physical (deskphone / SIP / paging) devices found.")
                            continue
                        for did in phys:
                            device_wants.setdefault(did, all_toggle)

                    if not device_wants:
                        _apply_log(store, 'info', f"• {g['label']}: nothing to apply.")
                        continue

                    rule_label = rule_key or f"default ({rule_id})"
                    ok, changed, msg = apply_device_toggles(
                        ext_id, schema, rule_id, device_wants,
                        auth_data=auth_data, task_id=task_id)
                    tag = f"{g['label']} · rule {rule_label}"
                    if ok and changed:
                        _apply_log(store, 'success', f"✅ {tag}: {msg}")
                        written += 1
                    elif ok:
                        _apply_log(store, 'info', f"• {tag}: {msg}")
                    else:
                        _apply_log(store, 'error', f"❌ {tag}: {msg}")
                except Exception as e:
                    _apply_log(store, 'error', f"❌ {g['label']} · rule {rule_key or 'default'}: {e}")
                # Gentle pacing between writes to stay under the rate limit.
                time.sleep(1.0)

        store['written'] = written
        store['status'] = 'completed'
        store['message'] = (f"Stopped — {written} rule(s) updated."
                            if store.get('cancelled')
                            else f"Finished — {written} rule(s) updated.")
    except Exception as e:
        store['status'] = 'error'
        store['error'] = str(e)
        _apply_log(store, 'error', f"Apply failed: {e}")
        store['message'] = f"Apply failed: {e}"
    finally:
        task_control.clear(task_id)


# ==========================================================================
# BLANK TEMPLATE
# ==========================================================================

def build_blank_template():
    """A blank, free-hand sheet for operators who don't want to run an audit
    first: type an extension number and a Yes/No. It applies to the DEFAULT
    business-hours rule; a blank Device ID means every physical phone on the
    extension. Returns the .xlsx bytes."""
    df = pd.DataFrame([], columns=TEMPLATE_COLS)
    guide = pd.DataFrame([
        {"Field": "Extension", "Required": "Yes",
         "Notes": "The user extension NUMBER (e.g. 101)."},
        {"Field": "Device ID", "Required": "Optional",
         "Notes": "Copy from an audit export to target ONE phone. Leave BLANK to apply "
                  "to ALL physical phones (deskphone / generic SIP / paging) on the extension."},
        {"Field": "Device Name", "Required": "No",
         "Notes": "Informational only — ignored on upload."},
        {"Field": "Ring Enabled", "Required": "Yes",
         "Notes": "Yes = the phone rings; No = it does not. Blank = leave unchanged."},
        {"Field": "Scope", "Required": "—",
         "Notes": "A blank template applies to the DEFAULT business-hours call-handling rule. "
                  "To change after-hours / custom rules, edit and re-upload an Audit export "
                  "instead (its rows carry the Rule IDs)."},
    ])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Device Ringing')
        ws = writer.sheets['Device Ringing']
        add_ring_toggle_dropdown(ws, TEMPLATE_COLS)
        for column in ws.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            ws.column_dimensions[column[0].column_letter].width = min(length + 5, 55)

        guide.to_excel(writer, index=False, sheet_name='Format Guide')
        gws = writer.sheets['Format Guide']
        for column in gws.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            gws.column_dimensions[column[0].column_letter].width = min(length + 5, 90)

    return output.getvalue()
