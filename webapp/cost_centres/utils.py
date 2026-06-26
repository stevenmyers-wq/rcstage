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

    # 2. Build Site to Cost Centre Map
    # We must fetch extensions first to map sites to their cost centres
    ext_params = {'status': ['Enabled', 'Disabled', 'NotActivated', 'Unassigned']}
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token, params=ext_params)
    
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

    # 3. Fetch Account Default Cost Centre
    account_default_cc_id = main_site_cc_id
    account_default_cc_name = main_site_cc_name
    try:
        acc_info = rc_api_call('/restapi/v1.0/account/~', method='GET', token=token)
        if acc_info and acc_info.get('costCenter') and acc_info['costCenter'].get('id'):
            account_default_cc_id = str(acc_info['costCenter']['id'])
            account_default_cc_name = acc_info['costCenter'].get('name') or cc_map.get(account_default_cc_id, f"Cost Centre {account_default_cc_id}")
    except Exception:
        pass

    def resolve_cost_centre(item):
        """Resolves the effective Cost Centre: Explicit Assignment -> Site -> Account Default"""
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
            
        return '', 'Account Default'

    assets = []
    
    # 4. Process Extensions
    for ext in extensions:
        if ext.get('type') in ['ApplicationExtension']:
            continue
            
        cc_id, cc_name = resolve_cost_centre(ext)
        
        name = ext.get('name', '')
        if not name:
            contact = ext.get('contact', {})
            name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
        
        # Tag unassigned users so they are identifiable
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
            'site': ext.get('site', {}).get('name', 'Main Site'),
            'department': ext.get('contact', {}).get('department', 'N/A') or 'N/A',
            'costCenterId': cc_id,
            'costCenterName': cc_name
        })

    # 5. Fetch Phone Numbers
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    for pn in phone_numbers:
        usage = pn.get('usageType', '')
        if usage in ['CompanyNumber', 'MainCompanyNumber', 'DirectNumber', 'ForwardedNumber'] and not pn.get('extension'):
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
            
    # 6. Fetch Devices
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

    # 7. Fetch Billing Items / Licenses
    try:
        licenses = fetch_all_pages('/restapi/v1.0/account/~/licenses', token=token)
        for lic in licenses:
            cc_id, cc_name = resolve_cost_centre(lic)
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
    asset_id = str(asset['id'])
    asset_type = asset['type']
    
    # Force string representation to avoid Javascript Int64 precision loss in RC Backend
    cc_id_str = str(cost_centre_id) if cost_centre_id else ""
    
    if asset_type == 'Extension':
        endpoint = f'/restapi/v1.0/account/~/extension/{asset_id}'
        payload = {'costCenter': {'id': cc_id_str}} if cc_id_str else {'costCenter': {}}
        rc_api_call(endpoint, method='PUT', json=payload, token=token, raise_error=True)
        
    elif asset_type == 'PhoneNumber':
        # The V1 Phone Number API silently ignores Cost Center updates. We MUST use V2 PATCH.
        endpoint = f'/restapi/v2/accounts/~/phone-numbers/{asset_id}'
        payload = {'costCenterId': cc_id_str} if cc_id_str else {'costCenterId': None}
        rc_api_call(endpoint, method='PATCH', json=payload, token=token, raise_error=True)
        
    elif asset_type == 'Device':
        endpoint = f'/restapi/v1.0/account/~/device/{asset_id}'
        payload = {'costCenter': {'id': cc_id_str}} if cc_id_str else {'costCenter': {}}
        rc_api_call(endpoint, method='PUT', json=payload, token=token, raise_error=True)
        
    elif asset_type == 'License':
        raise ValueError("RingCentral API does not support updating Cost Centres for standalone licenses.")
    else:
        raise ValueError(f"Unknown asset type: {asset_type}")

    return True
