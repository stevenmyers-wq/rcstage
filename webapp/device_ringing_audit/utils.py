import io
import os
import time
import requests
import base64
import pandas as pd
from datetime import datetime
from webapp.rc_api import rc_api_call
from webapp.auth_utils import get_impersonation_token

# Global store for background task progress
audit_progress_store = {}

def safe_api_call(endpoint, method='GET', auth_data=None, task_id=None):
    """Helper to safely request API data while respecting 429 Rate Limits and healing expired tokens."""
    for attempt in range(4):
        active_token = auth_data['access_token'] if auth_data else None
        resp = rc_api_call(endpoint, method=method, return_response=True, token=active_token)
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
                        except Exception:
                            pass
                            
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
                    except Exception:
                        pass
                        
            raise Exception("Authentication token expired during audit. Please Authorize & Bridge again.")
            
        # 3. Handle Success
        if resp and getattr(resp, 'ok', False):
            try:
                return resp.json()
            except:
                return {}
                
        # 4. Handle Server/Gateway Errors (50x)
        if status_code and status_code >= 500:
            time.sleep(3)
            continue
            
        # For 400 Bad Request, 403 Forbidden or 404 Not Found, return None (don't retry)
        return None
        
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

    # ==========================================
    # 1. V2 SCHEMA (PRIMARY)
    # ==========================================
    v2_state_rules_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"
    v2_state_resp = safe_api_call(v2_state_rules_url, auth_data=auth_data, task_id=task_id)
    
    is_v2 = False

    if v2_state_resp and 'records' in v2_state_resp:
        is_v2 = True
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
            for rule_summary in v1_resp['records']:
                if rule_summary.get('enabled', False) and rule_summary.get('callHandlingAction') == 'ForwardCalls':
                    rule_id = rule_summary.get('id')
                    
                    rule_detail_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}"
                    rule_detail = safe_api_call(rule_detail_url, auth_data=auth_data, task_id=task_id)
                    
                    if rule_detail and 'forwarding' in rule_detail:
                        r_name = rule_detail.get('name', rule_summary.get('name', 'Unnamed V1 Rule'))
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
                            'mobile_enabled': mobile_en,
                            'desktop_enabled': desktop_en,
                            'external_numbers': external_nums,
                            'device_status': dev_status
                        })
                    time.sleep(0.5)

    return device_map, rules_data

def run_audit_background(task_id, auth_data, ext_ids=None):
    """Background task to perform the audit, updating progress as it goes."""
    audit_progress_store[task_id] = {
        'status': 'running',
        'current': 0,
        'total': 0,
        'message': 'Fetching user list from RingCentral...',
        'file_data': None,
        'error': None
    }

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
            device_map, rules_data = get_device_ringing_status(ext_id, user_devices, auth_data, task_id=task_id)

            if not rules_data:
                rules_data = [{
                    'rule_name': 'No Enabled Rules Found',
                    'mobile_enabled': False,
                    'desktop_enabled': False,
                    'external_numbers': [],
                    'device_status': {did: False for did in device_map.keys()}
                }]

            for r_data in rules_data:
                row = {
                    "Username": ext_name,
                    "Extension": ext_num,
                    "Extension ID": ext_id,
                    "Rule Name": r_data['rule_name'],
                    "Mobile App Ring Enabled": "Yes" if r_data['mobile_enabled'] else "No",
                    "Desktop App Ring Enabled": "Yes" if r_data['desktop_enabled'] else "No",
                    "External Numbers Ringing": ", ".join(r_data['external_numbers']) if r_data['external_numbers'] else "None"
                }

                dev_idx = 1
                for did, d_info in device_map.items():
                    is_ringing = r_data['device_status'].get(did, False)
                    row[f"Device {dev_idx} Name"] = d_info['name']
                    row[f"Device {dev_idx} Ring Enabled"] = "Yes" if is_ringing else "No"
                    dev_idx += 1

                audit_data.append(row)
            
            # SUSTAINABLE PACING: Space out API calls to prevent rapid bucket depletion
            time.sleep(3.0)

        audit_progress_store[task_id]['message'] = "Compiling Excel Spreadsheet..."
        df = pd.DataFrame(audit_data)

        base_cols = ["Username", "Extension", "Extension ID", "Rule Name", "Mobile App Ring Enabled", "Desktop App Ring Enabled", "External Numbers Ringing"]
        dev_cols = [c for c in df.columns if c.startswith("Device")]
        dev_cols.sort(key=lambda x: int(x.split(' ')[1]) if len(x.split(' ')) > 1 and x.split(' ')[1].isdigit() else 999)

        final_cols = base_cols + dev_cols
        df = df[[c for c in final_cols if c in df.columns]]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Device Ringing Audit')
            worksheet = writer.sheets['Device Ringing Audit']
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

        audit_progress_store[task_id]['file_data'] = output.getvalue()
        audit_progress_store[task_id]['status'] = 'completed'
        audit_progress_store[task_id]['message'] = 'Audit successfully completed.'

    except Exception as e:
        audit_progress_store[task_id]['status'] = 'error'
        audit_progress_store[task_id]['error'] = str(e)
