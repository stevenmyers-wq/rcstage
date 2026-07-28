import time
from webapp.rc_api import rc_api_call


def get_license_debug_dump(token):
    """Diagnostic probe: hit every endpoint that could plausibly expose the
    license/product a given object consumes, and return the raw responses so
    we can determine what (if anything) RingCentral publishes for THIS account.

    RingCentral's public provisioning API (extension / phone-number / device
    records) does not carry an assigned-license field, and the seat/package a
    user holds (e.g. Digital Line vs Video Pro) cannot be derived from the
    extension `type`. This probe checks the remaining candidates before we
    conclude the data is unavailable.
    """
    result = {}

    def probe(label, endpoint, params=None):
        resp = rc_api_call(endpoint, method='GET', params=params,
                           token=token, return_response=True)
        entry = {'endpoint': endpoint, 'status': getattr(resp, 'status_code', None)}
        try:
            body = resp.json()
        except Exception:
            body = getattr(resp, 'text', '')
        # Trim record lists to the first 3 items to keep the dump readable.
        if isinstance(body, dict) and isinstance(body.get('records'), list):
            entry['record_count'] = len(body['records'])
            entry['sample_records'] = body['records'][:3]
            entry['navigation'] = body.get('navigation')
            entry['paging'] = body.get('paging')
        else:
            entry['body'] = body
        result[label] = entry

    # 1. Account-level license inventory (undocumented in older specs).
    probe('licenses_v1', '/restapi/v1.0/account/~/licenses')
    probe('licenses_v2', '/restapi/v2/accounts/~/licenses')

    # 2. Full phone-number record — check for any billing/product/feature field
    #    beyond usageType/paymentType.
    probe('phone_numbers_full', '/restapi/v1.0/account/~/phone-number',
          params={'perPage': 3})
    probe('phone_numbers_v2', '/restapi/v2/accounts/~/phone-numbers',
          params={'perPage': 3})

    # 3. Extension detail (list gives limited fields; detail/Full view may
    #    include a package/service descriptor).
    ext_list = rc_api_call('/restapi/v1.0/account/~/extension',
                           params={'perPage': 1, 'type': 'User'},
                           token=token, return_response=True)
    try:
        first_ext = (ext_list.json().get('records') or [{}])[0]
    except Exception:
        first_ext = {}
    ext_id = first_ext.get('id')
    if ext_id:
        probe('extension_detail', f'/restapi/v1.0/account/~/extension/{ext_id}')
        probe('extension_features',
              f'/restapi/v1.0/account/~/extension/{ext_id}/features')

    # 3b. CAN we attribute a specific SKU to a specific named user? Probe every
    #     candidate endpoint that might return a per-extension license mapping.
    if ext_id:
        probe('ext_licenses_v2', f'/restapi/v2/accounts/~/extensions/{ext_id}/licenses')
        probe('ext_licenses_v1', f'/restapi/v1.0/account/~/extension/{ext_id}/licenses')
    # Ask the v2 licenses endpoint for a per-assignment / extension-scoped view.
    probe('licenses_v2_detailed', '/restapi/v2/accounts/~/licenses',
          params={'view': 'Detailed', 'perPage': 5})
    if ext_id:
        probe('licenses_v2_by_ext', '/restapi/v2/accounts/~/licenses',
              params={'extensionId': ext_id})
    probe('licenses_v2_assignments', '/restapi/v2/accounts/~/license-assignments',
          params={'perPage': 5})

    # 4. Cost-center definitions (already used by the tab) for completeness.
    probe('cost_centers', '/restapi/v1.0/account/~/cost-center')

    return result

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


# ---------------------------------------------------------------------------
# License classification
# ---------------------------------------------------------------------------
# RingCentral does not expose the assigned license SKU on an extension, nor any
# per-extension license endpoint (every candidate 404s; the v2 licenses
# endpoint only returns per-cost-centre aggregates). We therefore attribute the
# license each object *consumes* using RingCentral's own model:
#   - a User extension consumes a Digital Line seat;
#   - a non-user extension (VirtualUser / Message-Only / Announcement / IVR /
#     Site / ...) that carries a phone number consumes an Additional Local
#     Number for that number;
#   - a non-user extension with no number consumes nothing billable.
# The authoritative per-cost-centre variant split (DL-UNL vs DL-BAS, ALN totals,
# etc.) comes from the v2 licenses endpoint and is surfaced separately as the
# License Inventory.

# Extension types that consume a Digital Line seat.
DIGITAL_LINE_EXTENSION_TYPES = {'User', 'DigitalUser'}

# Friendly names for the license SKU prefixes returned by /v2/.../licenses.
# skuId looks like 'LC_DL-UNL_50' -> prefix 'DL-UNL'. Unknown prefixes fall
# back to the raw SKU so nothing is silently mislabelled.
LICENSE_SKU_NAMES = {
    'DL-UNL': 'Digital Line Unlimited',
    'DL-BAS': 'Digital Line Basic (Metered)',
    'DL-HDSK': 'Digital Line (Hot Desk / Deskphone)',
    'FDL': 'Additional Digital Line',
    'ALN': 'Additional Local Number',
    'ALNSMS': 'Additional Local Number (SMS)',
    'ATN': 'Additional Toll-Free Number',
    'RS': 'RingCentral Rooms',
    'RCRM': 'RingCentral Rooms',
    'LR': 'Live Reports',
}


def decode_license_sku(sku_id):
    """'LC_DL-UNL_50' -> 'Digital Line Unlimited' (raw SKU if unknown)."""
    if not sku_id:
        return 'Unknown License'
    code = sku_id[3:] if sku_id.startswith('LC_') else sku_id
    code = code.rsplit('_', 1)[0]  # strip trailing numeric license-type id
    return LICENSE_SKU_NAMES.get(code, sku_id)


def classify_extension_license(ext_type, phone_number_count):
    """License an extension object consumes, per the rules above."""
    if ext_type in DIGITAL_LINE_EXTENSION_TYPES:
        return 'Digital Line'
    if phone_number_count > 0:
        return 'Additional Local Number' + (
            f' (x{phone_number_count})' if phone_number_count > 1 else '')
    return ''


def classify_phone_number_license(usage_type, payment_type):
    """License a standalone (extension-less) phone number consumes."""
    if usage_type == 'MainCompanyNumber':
        return 'Main Company Number'
    if payment_type == 'External':
        return 'External / Forwarded Number'
    if payment_type == 'TollFree' or usage_type == 'TollFreeNumber':
        return 'Additional Toll-Free Number'
    if usage_type in ('CompanyNumber', 'AdditionalCompanyNumber'):
        return 'Additional Company Number'
    if usage_type in ('ForwardedNumber', 'ForwardedCompanyNumber'):
        return 'Forwarded Number'
    if usage_type == 'DirectNumber':
        return 'Additional Local Number'
    return usage_type or 'Phone Number'


def build_license_inventory(token, cc_map):
    """Authoritative per-cost-centre license inventory from /v2/.../licenses.

    Returns a list of cost centres, each with its license SKUs and the
    assigned / available quantities -- the same breakdown the RingCentral Admin
    Portal Cost Center license report shows.
    """
    resp = rc_api_call('/restapi/v2/accounts/~/licenses', method='GET',
                       token=token, return_response=True)
    if not resp or getattr(resp, 'status_code', 500) != 200:
        return []
    try:
        licenses = resp.json().get('licenses', [])
    except Exception:
        return []

    # cc_id -> sku_id -> {name, sku, assigned, available}
    grouped = {}
    for lic in licenses:
        cc_id = str(lic.get('costCenterId', '') or '')
        sku = lic.get('skuId', '') or ''
        qty = lic.get('quantity', 0) or 0
        assigned = bool(lic.get('assigned'))
        node = grouped.setdefault(cc_id, {}).setdefault(sku, {
            'sku': sku,
            'name': decode_license_sku(sku),
            'assigned': 0,
            'available': 0,
        })
        if assigned:
            node['assigned'] += qty
        else:
            node['available'] += qty

    inventory = []
    for cc_id, skus in grouped.items():
        rows = sorted(skus.values(), key=lambda r: r['name'].lower())
        for r in rows:
            r['total'] = r['assigned'] + r['available']
        inventory.append({
            'costCenterId': cc_id,
            'costCenterName': cc_map.get(cc_id, f'Cost Centre {cc_id}'),
            'licenses': rows,
            'totalAssigned': sum(r['assigned'] for r in rows),
            'totalAvailable': sum(r['available'] for r in rows),
        })

    inventory.sort(key=lambda c: c['costCenterName'].lower())
    return inventory


def get_cost_centres_data(token):
    # 1. Fetch available cost centres and build a lookup map
    cost_centres = fetch_all_pages('/restapi/v1.0/account/~/cost-center', token=token)
    cc_map = {str(cc['id']): cc.get('name', f"Cost Centre {cc['id']}") for cc in cost_centres}

    # 2. Fetch the core object lists up front so we can build cost-centre name
    #    lookups and per-extension phone-number counts before classifying.
    ext_params = {'status': ['Enabled', 'Disabled', 'NotActivated', 'Unassigned']}
    extensions = fetch_all_pages('/restapi/v1.0/account/~/extension', token=token, params=ext_params)
    phone_numbers = fetch_all_pages('/restapi/v1.0/account/~/phone-number', token=token)
    devices = fetch_all_pages('/restapi/v1.0/account/~/device', token=token)

    # The dedicated /cost-center list endpoint 404s on some accounts, so harvest
    # cost-centre names from the costCenter objects embedded on each record.
    for _rec in list(extensions) + list(phone_numbers) + list(devices):
        _cc = _rec.get('costCenter') or {}
        _cid = str(_cc.get('id', '') or '')
        if _cid and _cc.get('name'):
            cc_map.setdefault(_cid, _cc.get('name'))

    # Per-extension phone-number count. A non-user extension that carries a
    # number consumes an Additional Local Number for it.
    ext_number_count = {}
    for _pn in phone_numbers:
        _ext = _pn.get('extension') or {}
        _eid = str(_ext.get('id', '') or '')
        if _eid:
            ext_number_count[_eid] = ext_number_count.get(_eid, 0) + 1

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
            'licenseType': classify_extension_license(
                ext.get('type'), ext_number_count.get(str(ext.get('id')), 0)),
            'costCenterId': cc_id,
            'costCenterName': cc_name
        })

    # 5. Process standalone Phone Numbers (not attached to an extension).
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
                'licenseType': classify_phone_number_license(usage, pn.get('paymentType')),
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })

    # 6. Process standalone Devices (not attached to an extension).
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
                'licenseType': 'Device',
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })

    # 7. Fetch account-level add-on Licenses (Live Reports, Rooms, etc.).
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
                'licenseType': l_type,
                'costCenterId': cc_id,
                'costCenterName': cc_name
            })
    except Exception as e:
        print(f"[Cost Centres] Licenses fetch skipped: {e}")

    # 8. Authoritative per-cost-centre license inventory (v2 endpoint).
    license_inventory = build_license_inventory(token, cc_map)

    # Cost-centre list for the dropdowns, built from every cost centre we saw
    # (the dedicated /cost-center list endpoint 404s on some accounts).
    cost_centres_out = sorted(
        [{'id': cid, 'name': name} for cid, name in cc_map.items()],
        key=lambda c: c['name'].lower())

    return {
        'cost_centres': cost_centres_out,
        'assets': assets,
        'license_inventory': license_inventory,
    }


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
