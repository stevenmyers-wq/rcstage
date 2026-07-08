import io
import time
import pandas as pd
from datetime import datetime
from webapp.rc_api import rc_api_call

# Global store for background task progress
audit_progress_store = {}

def safe_api_call(endpoint, method='GET', token=None, task_id=None):
    """Helper to safely request API data while respecting 429 Rate Limits and 50x errors."""
    for attempt in range(4):
        resp = rc_api_call(endpoint, method=method, return_response=True, token=token)
        status_code = getattr(resp, 'status_code', None)
        
        # 1. Handle Rate Limiting (429)
        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 10
            if task_id:
                audit_progress_store[task_id]['message'] = f"Rate limit hit! Pausing for {retry_after}s..."
            time.sleep(retry_after + 1)
            continue
            
        # 2. Handle Token Expiry (401)
        if status_code == 401:
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
            
        # For 403 Forbidden or 404 Not Found, return None (don't retry)
        return None
        
    return None

def fetch_all_users(token, task_id=None):
    """Fetches all users from the account."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", token=token, task_id=task_id)
        if not resp or 'records' not in resp: 
            break
        users.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return users

def get_device_ringing_status(ext_id, token, task_id=None):
    """
    Fetches the user's devices and evaluates ALL configured call handling rules.
    Queries V2 Schema first (Source of truth for migrated accounts), then falls back to V1.
    """
    devices_resp = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device", token=token, task_id=task_id)
    devices = devices_resp.get('records', []) if devices_resp else []
    
    # Store both the name and the TYPE of the device so we can correctly map SoftPhones
    device_map = {}
    for d in devices:
        device_map[str(d['id'])] = {
            'name': d.get('name') or d.get('model', {}).get('name', 'Unknown Device'),
            'type': d.get('type', 'Unknown')
        }

    rules_data = []

    # ==========================================
    # 1. V2 SCHEMA (PRIMARY)
    # ==========================================
    v2_state_rules_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"
    v2_state_resp = safe_api_call(v2_state_rules_url, token=token, task_id=task_id)
    
    is_v2 = False

    # If the state-rules endpoint responds with records, the user is on V2
    if v2_state_resp and 'records' in v2_state_resp:
        is_v2 = True
        v2_rules_to_check = []
        
        # Load State Rules (Business Hours, After Hours, DND, Agent, etc.)
        for rule in v2_state_resp['records']:
            # State rules nest their enabled flag inside the 'state' object
            if not rule.get('state', {}).get('enabled', True):
                continue
            v2_rules_to_check.append(rule)
            
        # Load Custom Interaction Rules
        v2_interaction_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
        v2_interaction_resp = safe_api_call(v2_interaction_url, token=token, task_id=task_id)
        if v2_interaction_resp and 'records' in v2_interaction_resp:
            for rule in v2_interaction_resp['records']:
                # Interaction rules keep their enabled flag at the top level
                if not rule.get('enabled', True):
                    continue
                v2_rules_to_check.append(rule)

        # Parse all collected V2 rules based on exact schema
        for rule in v2_rules_to_check:
            r_name = rule.get('displayName') or rule.get('name') or rule.get('id') or 'Unnamed V2 Rule'
            mobile_en = False
            desktop_en = False
            external_nums = []
            dev_status = {dev_id: False for dev_id in device_map.keys()}
            
            # Actions can live in 'dispatching' or a nested 'dispatchingRef' object
            dispatching = rule.get('dispatching') or rule.get('dispatchingRef', {}).get('dispatching') or {}
            actions = dispatching.get('actions', [])
            
            for action in actions:
                # If an action explicitly disables itself, skip
                if action.get('enabled', True) == False:
                    continue
                    
                a_type = action.get('type', '')
                targets = action.get('targets', [])
                
                for t in targets:
                    if t.get('enabled', True) == False:
                        continue
                        
                    t_type = t.get('type', '')
                    
                    # Normal ringing targets
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
                                
                    # External forwarding targets (often stored as Terminating actions instead of Ring Groups)
                    elif a_type == 'TerminatingAction' and t_type == 'PhoneNumberTerminatingTarget':
                        # Can be under 'destination.phoneNumber' or just 'phoneNumber'
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
        v1_resp = safe_api_call(v1_rules_url, token=token, task_id=task_id)

        if v1_resp and 'records' in v1_resp:
            for rule_summary in v1_resp['records']:
                if rule_summary.get('enabled', False) and rule_summary.get('callHandlingAction') == 'ForwardCalls':
                    rule_id = rule_summary.get('id')
                    
                    # Explicit fetch per rule to guarantee forwarding nested array is populated
                    rule_detail_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}"
                    rule_detail = safe_api_call(rule_detail_url, token=token, task_id=task_id)
                    
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

def run_audit_background(task_id, token):
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
        users = fetch_all_users(token, task_id=task_id)
        valid_users = [u for u in users if u.get('status') in ['Enabled', 'NotActivated']]
        
        total_users = len(valid_users)
        audit_progress_store[task_id]['total'] = total_users
        
        if total_users == 0:
            audit_progress_store[task_id]['status'] = 'error'
            audit_progress_store[task_id]['error'] = 'No active users found to audit.'
            return

        audit_data = []

        for i, user in enumerate(valid_users):
            ext_id = str(user['id'])
            ext_name = user.get('name', 'Unknown')
            ext_num = user.get('extensionNumber', '')

            audit_progress_store[task_id]['current'] = i + 1
            audit_progress_store[task_id]['message'] = f"Auditing Extension {ext_num} ({ext_name})..."

            device_map, rules_data = get_device_ringing_status(ext_id, token, task_id=task_id)

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
                    
                    # SoftPhone inheritance trap: V2 API addresses softphones as "Desktop Apps"
                    if not is_ringing and d_info['type'] == 'SoftPhone':
                        if r_data['desktop_enabled']:
                            is_ringing = True
                            
                    row[f"Device {dev_idx} Name"] = d_info['name']
                    row[f"Device {dev_idx} Ring Enabled"] = "Yes" if is_ringing else "No"
                    dev_idx += 1

                audit_data.append(row)
            
            # SUSTAINABLE PACING: Space out API calls to prevent 429 lockouts
            time.sleep(3.5)

        # Build Excel File
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
