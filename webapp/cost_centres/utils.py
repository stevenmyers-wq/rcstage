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
            print(f"[Cost Centres] Endpoint {endpoint} returned empty or failed.")
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

    # 2. Fetch Account Default Cost Centre
    account_default_cc_id = None
    account_default_cc_name = 'Account Default'
    try:
        acc_info = rc_api_call('/restapi/v1.0/account/~', method='GET', token=token)
        if acc_info and acc_info.get('costCenter') and acc_info['costCenter'].get('id'):
            account_default_cc_id = str(acc_info['costCenter']['id'])
            account_default_cc_name = acc_info['costCenter'].get('name') or cc_map.get(account_default_cc_id, f"Cost Centre {account_default_cc_id}")
    except Exception as e:
        print(f"[Cost Centres] Failed to fetch account default CC: {e}")

    def resolve_cost_centre(item):
        """Resolves the effective Cost Centre: Explicit Assignment -> Account Default"""
        # NO INHERITANCE GUESSING. Trust the payload explicitly.
        cc_id = str(item.get('costCenter', {}).get('id', ''))
        if cc_id:
            cc_name = item.get('costCenter', {}).get('name') or cc_map.get(cc_id, f"Cost Centre {cc_id}")
            return cc_id, cc_name
            
        # Fallback to Account Default if not explicitly assigned
        if account_default_cc_id:
            return account_default_cc_id, account_default_cc_name
            
        return '', 'Account Default'

    assets = []
    
    # 3. Fetch ALL Extensions (crucially, we must request 'Unassigned' status explicitly)
    ext_params = {'status': ['Enabled', 'Disabled', 'NotActivated', 'Unassigned']}
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token, params=ext_params)
    
    for ext in extensions:
        cc_id, cc_name = resolve_cost_centre(ext)
        
        # Format name nicely for unassigned users
        name = ext.get('name', '')
        if not name:
            contact = ext.get('contact', {})
            name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
        if not name:
            name = "Unassigned / Unnamed Extension"
            
        assets.append({
            'id': str(ext.get('id')),
            'type': 'Extension',
            'subType': ext.get('type', 'Unknown'),
            'status': ext.get('status', 'Unknown'),
            'name': name,
            'number': ext.get('extensionNumber', 'N/A'),
            'site': ext.get('site', {}).get('name', 'Main Site'),
            'department': ext.get('contact', {}).get('department', 'N/A') or 'N/A',
            'costCenterId': cc_id,
            'costCenterName': cc_name
        })

    # 4. Fetch Phone Numbers
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    for pn in phone_numbers:
        usage = pn.get('usageType', '')
        # ForwardedNumber is sometimes used for unassigned external routing logic
        if usage in ['CompanyNumber', 'MainCompanyNumber', 'DirectNumber', 'ForwardedNumber'] and not pn.get('extension'):
            cc_id, cc_name = resolve_cost_centre(pn)
            assets.append({
                'id': str(pn.get('id')),
                'type': 'PhoneNumber',
                'subType': usage,
                'status': pn.get('status', 'Unknown'),
                'name': pn.get('phoneNumber', ''),
                'number': pn.get('phoneNumber', ''),
                'site': 'N/A',
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })
            
    # 5. Fetch Devices
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token=token)
    for dev in devices:
        if not dev.get('extension'):
            cc_id, cc_name = resolve_cost_centre(dev)
            assets.append({
                'id': str(dev.get('id')),
                'type': 'Device',
                'subType': dev.get('type', 'Unknown'),
                'status': dev.get('status', 'Unknown'),
                'name': dev.get('name') or dev.get('model', {}).get('name', 'Unknown Device'),
                'number': dev.get('serial', 'N/A'),
                'site': dev.get('site', {}).get('name', 'Main Site'),
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })

    # 6. Fetch Licenses (captures standalone ACE/Live Reports items that aren't extensions)
    try:
        licenses = fetch_all_pages('/restapi/v1.0/account/~/licenses', token=token)
        for lic in licenses:
            cc_id, cc_name = resolve_cost_centre(lic)
            l_type = lic.get('type', {}).get('name', 'License')
            assets.append({
                'id': str(lic.get('id')),
                'type': 'License',
                'subType': l_type,
                'status': 'Unassigned',
                'name': f"{l_type} License",
                'number': 'N/A',
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
