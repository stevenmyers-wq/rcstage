import time
from webapp.rc_api import rc_api_call

def fetch_all_pages(endpoint, token, params=None):
    if params is None:
        params = {}
    params['perPage'] = 1000
    params['page'] = 1
    records = []
    
    while True:
        resp = rc_api_call(endpoint, method='GET', params=params, token=token)
        if not resp or 'records' not in resp: 
            break
        records.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        params['page'] += 1
        time.sleep(0.05)  # Pace to avoid rate limits
        
    return records

def get_cost_centres_data(token):
    # 1. Fetch available cost centres
    cost_centres = fetch_all_pages('/restapi/v1.0/account/~/cost-center', token=token)
    
    assets = []
    
    # 2. Fetch Extensions (Users, IVRs, Queues, etc.)
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token)
    for ext in extensions:
        # We skip lines/ports that typically don't have cost centers assignable in bulk
        if ext.get('type') in ['Limited', 'ApplicationExtension']:
            continue
            
        assets.append({
            'id': str(ext.get('id')),
            'type': 'Extension',
            'subType': ext.get('type', 'Unknown'),
            'name': ext.get('name', 'Unknown'),
            'number': ext.get('extensionNumber', 'N/A'),
            'site': ext.get('site', {}).get('name', 'Main Site'),
            'department': ext.get('contact', {}).get('department', 'N/A') or 'N/A',
            'costCenterId': ext.get('costCenter', {}).get('id', ''),
            'costCenterName': ext.get('costCenter', {}).get('name', 'Unassigned')
        })

    # 3. Fetch Phone Numbers (Company numbers / Unassigned DIDs)
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    for pn in phone_numbers:
        usage = pn.get('usageType', '')
        # Only grab numbers NOT tied to an extension (to avoid duplication with the extension list)
        if usage in ['CompanyNumber', 'MainCompanyNumber', 'DirectNumber'] and not pn.get('extension'):
            assets.append({
                'id': str(pn.get('id')),
                'type': 'PhoneNumber',
                'subType': usage,
                'name': pn.get('phoneNumber', ''),
                'number': pn.get('phoneNumber', ''),
                'site': 'N/A',
                'department': 'N/A',
                'costCenterId': pn.get('costCenter', {}).get('id', ''),
                'costCenterName': pn.get('costCenter', {}).get('name', 'Unassigned')
            })
            
    # 4. Fetch Devices (Unassigned hard phones)
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token=token)
    for dev in devices:
        # Only grab unassigned devices
        if not dev.get('extension'):
            assets.append({
                'id': str(dev.get('id')),
                'type': 'Device',
                'subType': dev.get('type', 'Unknown'),
                'name': dev.get('name') or dev.get('model', {}).get('name', 'Unknown Device'),
                'number': dev.get('serial', 'N/A'),
                'site': dev.get('site', {}).get('name', 'Main Site'),
                'department': 'N/A',
                'costCenterId': dev.get('costCenter', {}).get('id', ''),
                'costCenterName': dev.get('costCenter', {}).get('name', 'Unassigned')
            })

    return {'cost_centres': cost_centres, 'assets': assets}

def update_asset_cost_centre(token, asset, cost_centre_id):
    asset_id = asset['id']
    asset_type = asset['type']
    
    # RingCentral expects the ID as a string, but an empty string unassigns it (if supported)
    payload = {'costCenter': {'id': str(cost_centre_id)}}
    
    if asset_type == 'Extension':
        endpoint = f'/restapi/v1.0/account/~/extension/{asset_id}'
    elif asset_type == 'PhoneNumber':
        endpoint = f'/restapi/v1.0/account/~/phone-number/{asset_id}'
    elif asset_type == 'Device':
        endpoint = f'/restapi/v1.0/account/~/device/{asset_id}'
    else:
        raise ValueError(f"Unknown asset type: {asset_type}")

    resp = rc_api_call(endpoint, method='PUT', json=payload, token=token, return_response=True)
    
    if not getattr(resp, 'ok', False):
        err = "Update failed"
        try: 
            err = resp.json().get('message', err)
        except: 
            pass
        raise Exception(err)
        
    return True
