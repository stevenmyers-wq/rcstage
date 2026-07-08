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
    Fetches the user's devices and queries the Call Handling APIs to map which devices are enabled to ring.
    Safely handles V1 Forwarding rules, V2 Default Rules, and V2 Interaction Rules.
    """
    devices_resp = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device", token=token, task_id=task_id)
    devices = devices_resp.get('records', []) if devices_resp else []
    device_map = {
        str(d['id']): d.get('name') or d.get('model', {}).get('name', 'Unknown Device') 
        for d in devices
    }

    mobile_enabled = False
    desktop_enabled = False
    device_status = {dev_id: False for dev_id in device_map.keys()}

    # 1. Try V1 Business Hours Rule first
    v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule"
    v1_resp = safe_api_call(v1_url, token=token, task_id=task_id)
    
    v1_success = False

    if v1_resp and v1_resp.get('callHandlingAction') == 'ForwardCalls' and 'forwarding' in v1_resp:
        v1_success = True
        rules = v1_resp['forwarding'].get('rules', [])
        for r in rules:
            if r.get('enabled', True) or r.get('active', True):
                for f in r.get('forwardingNumbers', []):
                    f_type = f.get('type', '')
                    f_device_id = str(f.get('device', {}).get('id', ''))
                    
                    if f_type in ['SoftPhone', 'ApplicationExtension']:
                        desktop_enabled = True
                        mobile_enabled = True
                    elif f_device_id and f_device_id in device_status:
                        device_status[f_device_id] = True

    # 2. If V1 failed (403 Forbidden), account is migrated to V2 Schema.
    if not v1_success:
        v2_rules_to_check = []
        
        # A. Fetch V2 Default Rule (Standard routing)
        v2_default_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/default-rule"
        v2_default_resp = safe_api_call(v2_default_url, token=token, task_id=task_id)
        if v2_default_resp and 'dispatching' in v2_default_resp:
            v2_rules_to_check.append(v2_default_resp)
            
        # B. Fetch V2 Interaction Rules (Custom routing/exceptions)
        v2_interaction_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
        v2_interaction_resp = safe_api_call(v2_interaction_url, token=token, task_id=task_id)
        if v2_interaction_resp and 'records' in v2_interaction_resp:
            v2_rules_to_check.extend(v2_interaction_resp['records'])

        # C. Parse all collected V2 rules
        for rule in v2_rules_to_check:
            if not rule.get('enabled', True):
                continue
                
            actions = rule.get('dispatching', {}).get('actions', [])
            for action in actions:
                if action.get('type') == 'RingGroupAction' and action.get('enabled', True):
                    for t in action.get('targets', []):
                        if t.get('enabled', True):
                            t_type = t.get('type')
                            if t_type == 'AllMobileRingTarget':
                                mobile_enabled = True
                            elif t_type == 'AllDesktopRingTarget':
                                desktop_enabled = True
                            elif t_type == 'DeviceRingTarget':
                                dev_id = str(t.get('device', {}).get('id', ''))
                                if dev_id in device_status:
                                    device_status[dev_id] = True

    return mobile_enabled, desktop_enabled, device_map, device_status

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

            # Report specific item to the system log
            audit_progress_store[task_id]['current'] = i + 1
            audit_progress_store[task_id]['message'] = f"Auditing Extension {ext_num} ({ext_name})..."

            mobile_en, desktop_en, device_map, device_status = get_device_ringing_status(ext_id, token, task_id=task_id)

            row = {
                "Username": ext_name,
                "Extension": ext_num,
                "Extension ID": ext_id,
                "Mobile App Ring Enabled": "Yes" if mobile_en else "No",
                "Desktop App Ring Enabled": "Yes" if desktop_en else "No"
            }

            dev_idx = 1
            for did, dname in device_map.items():
                row[f"Device {dev_idx} Name"] = dname
                row[f"Device {dev_idx} Ring Enabled"] = "Yes" if device_status[did] else "No"
                dev_idx += 1

            audit_data.append(row)
            
            # SUSTAINABLE PACING: 2.5s prevents hitting the ~40 calls/min limit.
            time.sleep(2.5)

        # Build Excel File
        audit_progress_store[task_id]['message'] = "Compiling Excel Spreadsheet..."
        df = pd.DataFrame(audit_data)

        base_cols = ["Username", "Extension", "Extension ID", "Mobile App Ring Enabled", "Desktop App Ring Enabled"]
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
