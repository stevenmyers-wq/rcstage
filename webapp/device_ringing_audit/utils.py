import time
from webapp.rc_api import rc_api_call

def safe_api_call(endpoint, method='GET'):
    """Helper to safely request API data while respecting 429 Rate Limits."""
    for attempt in range(4):
        resp = rc_api_call(endpoint, method=method, return_response=True)
        if resp and getattr(resp, 'status_code', None) == 429:
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

def fetch_all_users():
    """Fetches all users from the account."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}")
        if not resp or 'records' not in resp: 
            break
        users.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return users

def get_device_ringing_status(ext_id):
    """
    Fetches the user's devices and queries the NEW V2 Call Handling (interaction-rules) API 
    to map which devices are enabled to ring.
    """
    # 1. Fetch physical devices to map device IDs to names
    devices_resp = safe_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device")
    devices = devices_resp.get('records', []) if devices_resp else []
    device_map = {
        str(d['id']): d.get('name') or d.get('model', {}).get('name', 'Unknown Device') 
        for d in devices
    }

    mobile_enabled = False
    desktop_enabled = False
    device_status = {dev_id: False for dev_id in device_map.keys()}

    # 2. Query V2 Interaction Rules API
    v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
    v2_resp = safe_api_call(v2_url)

    used_v2 = False
    if v2_resp and 'records' in v2_resp:
        used_v2 = True
        for rule in v2_resp['records']:
            # Typically, we want the default or business hours rule. 
            # We iterate through dispatching targets to find the ring group actions.
            actions = rule.get('dispatching', {}).get('actions', [])
            for action in actions:
                if action.get('type') == 'RingGroupAction':
                    # If the Ring Group Action itself is disabled, nothing in it rings
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
        # Fallback to V1 if V2 is unavailable or not migrated yet
        v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule"
        v1_resp = safe_api_call(v1_url)
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
