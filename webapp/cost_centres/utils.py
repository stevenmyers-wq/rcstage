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
            print(f"[Cost Centres] Endpoint {endpoint} did not return 'records'. Response: {str(resp)[:200]}")
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

    # 2. Fetch ALL Extensions
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token)
    
    # 3. Build Site to Cost Centre Map from the Extensions list
    site_cc_map = {}
    main_site_cc_id = None
    main_site_cc_name = None
    
    for ext in extensions:
        if ext.get('type') == 'Site' or ext.get('id') == 'main-site':
            site_id = str(ext.get('id', ''))
            cc_id = str(ext.get('costCenter', {}).get('id', ''))
            if cc_id:
                cc_name = ext.get('costCenter', {}).get('name') or cc_map.get(cc_id, f"Cost Centre {cc_id}")
                site_cc_map[site_id] = {'id': cc_id, 'name': cc_name}
                if site_id == 'main-site' or ext.get('name') == 'Main Site':
                    main_site_cc_id = cc_id
                    main_site_cc_name = cc_name

    # 4. Fetch Account Default Cost Centre
    account_default_cc_id = main_site_cc_id
    account_default_cc_name = main_site_cc_name
    try:
        acc_info = rc_api_call('/restapi/v1.0/account/~', method='GET', token=token)
        if acc_info and acc_info.get('costCenter') and acc_info['costCenter'].get('id'):
            account_default_cc_id = str(acc_info['costCenter']['id'])
            account_default_cc_name = acc_info['costCenter'].get('name') or cc_map.get(account_default_cc_id, f"Cost Centre {account_default_cc_id}")
    except Exception as e:
        print(f"[Cost Centres] Failed to fetch account default CC: {e}")

    def resolve_cost_centre(item):
        """Resolves the effective Cost Centre by checking Item -> Site -> Account inheritance"""
        # A. Check explicit item assignment
        cc_id = str(item.get('costCenter', {}).get('id', ''))
        if cc_id:
            cc_name = item.get('costCenter', {}).get('name') or cc_map.get(cc_id, f"Cost Centre {cc_id}")
            return cc_id, cc_name
        
        # B. Check site assignment
        site_id = str(item.get('site', {}).get('id', ''))
        if site_id and site_id in site_cc_map:
            return site_cc_map[site_id]['id'], site_cc_map[site_id]['name']
            
        # C. Fallback to Main Site / Account default
        if account_default_cc_id:
            return account_default_cc_id, account_default_cc_name
            
        return '', 'Unassigned'

    assets = []
    
    # 5. Process Extensions
    for ext in extensions:
        if ext.get('type') in ['Limited', 'ApplicationExtension']:
            continue
            
        cc_id, cc_name = resolve_cost_centre(ext)
            
        assets.append({
            'id': str(ext.get('id')),
            'type': 'Extension',
            'subType': ext.get('type', 'Unknown'),
            'name': ext.get('name', 'Unknown'),
            'number': ext.get('extensionNumber', 'N/A'),
            'site': ext.get('site', {}).get('name', 'Main Site'),
            'department': ext.get('contact', {}).get('department', 'N/A') or 'N/A',
            'costCenterId': cc_id,
            'costCenterName': cc_name
        })

    # 6. Fetch Phone Numbers
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    for pn in phone_numbers:
        usage = pn.get('usageType', '')
        if usage in ['CompanyNumber', 'MainCompanyNumber', 'DirectNumber'] and not pn.get('extension'):
            cc_id, cc_name = resolve_cost_centre(pn)
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
            
    # 7. Fetch Devices
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token=token)
    for dev in devices:
        if not dev.get('extension'):
            cc_id, cc_name = resolve_cost_centre(dev)
            assets.append({
                'id': str(dev.get('id')),
                'type': 'Device',
                'subType': dev.get('type', 'Unknown'),
                'name': dev.get('name') or dev.get('model', {}).get('name', 'Unknown Device'),
                'number': dev.get('serial', 'N/A'),
                'site': dev.get('site', {}).get('name', 'Main Site'),
                'department': 'N/A',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })

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
    else:
        raise ValueError(f"Unknown asset type: {asset_type}")

    resp = rc_api_call(endpoint, method='PUT', json=payload, token=token, return_response=True)
    
    if not getattr(resp, 'ok', False):
        err = "Update failed"
        try: err = resp.json().get('message', err)
        except: pass
        raise Exception(err)
        
    return True
