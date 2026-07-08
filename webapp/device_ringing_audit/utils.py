import io
import time
import pandas as pd
from datetime import datetime
from webapp.rc_api import rc_api_call

# Global store for background task progress
audit_progress_store = {}

def safe_api_call(endpoint, method='GET', token=None):
    """Helper to safely request API data while respecting 429 Rate Limits."""
    for attempt in range(4):
        resp = rc_api_call(endpoint, method=method, return_response=True, token=token)
        if resp and getattr(resp, 'status_code', None) == 429:
            # Respect RingCentral's requested backoff time
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 10
            time.sleep(retry_after)
            continue
        if resp and getattr(resp, 'ok', False):
            try:
                return resp.json()
            except:
                return {}
        return None
    return None

def fetch_all_users(token):
    """Fetches all users from the account."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp: 
            break
        users.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return users

def get_device_ringing_status(ext_id, token):
    """
    Fetches the user's devices and queries the NEW V2 Call Handling (interaction-rules) API 
    to map which devices are enabled to ring.
    """
    devices_resp = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device", token=token)
    devices = devices_resp.get('records', []) if devices_resp else []
    device_map = {
        str(d['id']): d.get('name') or d.get('model', {}).get('name', 'Unknown Device') 
        for d in devices
    }

    mobile_enabled = False
    desktop_enabled = False
    device_status = {dev_id: False for dev_id in device_map.keys()}

    v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
    v2_resp = safe_api_call(v2_url, token=token)

    used_v2 = False
    if v2_resp and 'records' in v2_resp:
        used_v2 = True
        for rule in v2_resp['records']:
            actions = rule.get('dispatching', {}).get('actions', [])
            for action in actions:
                if action.get('type') == 'RingGroupAction':
                    action_enabled = action.get('enabled', True)
                    if action_enabled:
                        targets = action.get('targets', [])
                        for t in targets:
                            t_type = t.get('type')
                            t_enabled = t.get('enabled', True)
                            
                            if t_type == 'AllMobileRingTarget':
                                mobile_enabled = mobile_enabled or t_enabled
                            elif t_type == 'AllDesktopRingTarget':
                                desktop_enabled = desktop_enabled or t_enabled
                            elif t_type == 'DeviceRingTarget':
                                dev_id = str(t.get('device', {}).get('id', ''))
                                if dev_id in device_status:
                                    device_status[dev_id] = device_status[dev_id] or t_enabled

    if not used_v2:
        # Fallback to V1
        v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule"
        v1_resp = safe_api_call(v1_url, token=token)
        if v1_resp and 'forwarding' in v1_resp:
            rules = v1_resp['forwarding'].get('rules', [])
            for r in rules:
                if r.get('active', True):
                    for f in r.get('forwardingNumbers', []):
                        f_type = f.get('type')
                        f_id = str(f.get('id', ''))
                        if f_type == 'SoftPhone':
                            desktop_enabled = True
                            mobile_enabled = True
                        elif f_id in device_status:
                            device_status[f_id] = True

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
        users = fetch_all_users(token)
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

            # Update progress state for the live UI log
            audit_progress_store[task_id]['current'] = i + 1
            audit_progress_store[task_id]['message'] = f"Auditing Extension {ext_num} ({ext_name})..."

            mobile_en, desktop_en, device_map, device_status = get_device_ringing_status(ext_id, token)

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
            # Gentle pacing to avoid instant bucket depletion on large accounts
            time.sleep(0.05)

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
