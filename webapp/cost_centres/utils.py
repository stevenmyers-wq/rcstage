import time
from webapp.rc_api import rc_api_call

def fetch_all_pages(endpoint, token, params=None):
    if params is None:
        params = {}
    params['perPage'] = 250
    params['page'] = 1
    records = []
    
    while True:
        resp = rc_api_call(endpoint, method='GET', params=params, token=token, raise_error=False)
        if not resp: 
            break
        if 'records' not in resp: 
            break
        records.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        params['page'] += 1
        time.sleep(0.05)
        
    return records

def get_cost_centres_data(token):
    # 1. Fetch available cost centres and build a lookup map
    cost_centres = fetch_all_pages('/restapi/v1.0/account/~/cost-center', token=token)
    cc_map = {str(cc['id']): cc.get('name', f"Cost Centre {cc['id']}") for cc in cost_centres}

    assets = []
    
    # 2. Fetch Extensions (including explicitly requesting 'Unassigned')
    ext_params = {'status': ['Enabled', 'Disabled', 'NotActivated', 'Unassigned']}
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token, params=ext_params)
    
    for ext in extensions:
        # Skip pure system objects
        if ext.get('type') in ['ApplicationExtension']:
            continue
            
        cc_id = str(ext.get('costCenter', {}).get('id', ''))
        cc_name = ext.get('costCenter', {}).get('name') or cc_map.get(cc_id, 'Unassigned')
        
        name = ext.get('name', '')
        if not name:
            contact = ext.get('contact', {})
            name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
            
        if ext.get('status') == 'Unassigned':
            name = f"[Unassigned] {name}" if name else "[Unassigned] Unknown Extension"
        elif not name:
            name = "Unnamed Extension"
            
        assets.append({
            'id': str(ext.get('id')),
            'type': 'Extension',
            'subType': ext.get('type', 'Unknown'),
            'name': name,
            'number': ext.get('extensionNumber', 'N/A'),
            'site': ext.get('site', {}).get('name', 'N/A'),
            'department': ext.get('contact', {}).get('department', 'N/A') or 'N/A',
            'costCenterId': cc_id,
            'costCenterName': cc_name
        })

    # 3. Fetch Phone Numbers
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    for pn in phone_numbers:
        usage = pn.get('usageType', '')
        # ForwardedNumber is sometimes used for unassigned external routing logic
        if usage in ['CompanyNumber', 'MainCompanyNumber', 'DirectNumber', 'ForwardedNumber'] and not pn.get('extension'):
            cc_id = str(pn.get('costCenter', {}).get('id', ''))
            cc_name = pn.get('costCenter', {}).get('name') or cc_map.get(cc_id, 'Unassigned')
            assets.append({
                'id': str(pn.get('id')),
                'type': 'PhoneNumber',
                'subType': usage,
                'name': pn.get('phoneNumber', ''),
                'number': pn.get('phoneNumber', ''),
                'site': 'N/A',
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })
            
    # 4. Fetch Devices (Rental hardphones)
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token=token)
    for dev in devices:
        if not dev.get('extension'):
            cc_id = str(dev.get('costCenter', {}).get('id', ''))
            cc_name = dev.get('costCenter', {}).get('name') or cc_map.get(cc_id, 'Unassigned')
            assets.append({
                'id': str(dev.get('id')),
                'type': 'Device',
                'subType': dev.get('type', 'Unknown'),
                'name': dev.get('name') or dev.get('model', {}).get('name', 'Unknown Device'),
                'number': dev.get('serial', 'N/A'),
                'site': dev.get('site', {}).get('name', 'N/A'),
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })

    # 5. Fetch Billing Items / Licenses (ACE, Boosters, Plans)
    try:
        licenses = fetch_all_pages('/restapi/v1.0/account/~/licenses', token=token)
        for lic in licenses:
            cc_id = str(lic.get('costCenter', {}).get('id', ''))
            cc_name = lic.get('costCenter', {}).get('name') or cc_map.get(cc_id, 'Unassigned')
            l_type = lic.get('type', {}).get('name', 'License')
            assets.append({
                'id': str(lic.get('id')),
                'type': 'License',
                'subType': 'Unassigned Licence',
                'name': l_type,
                'number': f"Qty: {lic.get('quantity', '1')}",
                'site': 'N/A',
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })
    except Exception as e:
        print(f"[Cost Centres] Licenses fetch skipped: {e}")

    return {'cost_centres': cost_centres, 'assets': assets}

def update_asset_cost_centre(token, asset, cost_centre_id):
    asset_id = asset['id']
    asset_type = asset['type']
    
    payload = {'costCenter': {'id': str(cost_centre_id)}}
    
    if asset_type == 'Extension':
        endpoint = f'/restapi/v1.0/account/~/extension/{asset_id}'
    elif asset_type == 'PhoneNumber':
        endpoint = f'/restapi/v1.0/account/~/phone-number/{asset_id}'
    elif asset_type == 'Device':
        endpoint = f'/restapi/v1.0/account/~/device/{asset_id}'
    elif asset_type == 'License':
        endpoint = f'/restapi/v1.0/account/~/licenses/{asset_id}'
    else:
        raise ValueError(f"Unknown asset type: {asset_type}")

    resp = rc_api_call(endpoint, method='PUT', json=payload, token=token, return_response=True)
    
    if not getattr(resp, 'ok', False):
        err = "Update failed"
        try: err = resp.json().get('message', err)
        except: pass
        raise Exception(err)
        
    return True
