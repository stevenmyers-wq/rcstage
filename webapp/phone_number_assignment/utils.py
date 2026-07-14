import io
import time
import pandas as pd
import requests
from flask import current_app
from webapp.rc_api import rc_api_call

def fetch_all_pages(endpoint, token, params=None):
    if params is None: 
        params = {}
    params['perPage'] = 1000
    params['page'] = 1
    records = []
    
    while True:
        resp = rc_api_call(endpoint, method='GET', params=params, token=token, raise_error=False)
        if not resp or 'records' not in resp: 
            break
        records.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        params['page'] += 1
        time.sleep(0.05)
        
    return records

def fetch_inventory_numbers(token):
    numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    
    inventory = []
    for n in numbers:
        if not n.get('extension') or not n.get('extension').get('id'):
            inventory.append(n)
            
    return inventory

def fetch_extensions(token):
    return fetch_all_pages('/restapi/v1.0/account/~/extension', token)

def generate_template(token):
    inventory = fetch_inventory_numbers(token)
    extensions = fetch_extensions(token)

    inv_data = [{
        'Available Phone Number': n.get('phoneNumber', ''),
        'Payment Type': n.get('paymentType', ''),
        'Usage Type': n.get('usageType', 'Inventory')
    } for n in inventory]
    
    ext_data = [{
        'Extension Number': e.get('extensionNumber', ''),
        'Extension Name': e.get('name', 'Unknown'),
        'Type': e.get('type', ''),
        'Extension ID (Reference)': e.get('id', '')
    } for e in extensions if e.get('status') in ['Enabled', 'NotActivated']]

    df_template = pd.DataFrame(columns=['Phone Number', 'Extension Number'])
    df_inv = pd.DataFrame(inv_data)
    df_ext = pd.DataFrame(ext_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_template.to_excel(writer, index=False, sheet_name='Assignment Template')
        
        if not df_inv.empty:
            df_inv.to_excel(writer, index=False, sheet_name='Available Numbers')
        else:
            pd.DataFrame([{"Available Phone Number": "No numbers available in inventory."}]).to_excel(writer, index=False, sheet_name='Available Numbers')
            
        if not df_ext.empty:
            df_ext.to_excel(writer, index=False, sheet_name='Available Extensions')

        for sheet in writer.sheets.values():
            for column in sheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                sheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
                
    output.seek(0)
    return output

def process_assignments(records, token):
    # Map phone string to internal numerical ID
    all_numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    phone_map = {}
    
    for n in all_numbers:
        if n.get('phoneNumber'):
            phone_num = n['phoneNumber'].strip()
            phone_map[phone_num] = str(n.get('id', ''))
            phone_map[phone_num.replace('+', '')] = str(n.get('id', ''))

    # Map Extension short Number to system internal long ID
    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_map = {}
    for e in all_exts:
        if e.get('extensionNumber'):
            ext_map[str(e['extensionNumber']).strip()] = str(e['id'])

    # Determine base service portal domain context dynamically (e.g. platform -> service)
    base_url = current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    service_base_url = base_url.replace('platform.', 'service.')
    endpoint_url = f"{service_base_url}/mobile/api/billing/assignNumbers"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    logs = []
    
    for index, row in enumerate(records):
        phone = str(row.get('Phone Number', '')).strip()
        ext_num = str(row.get('Extension Number', '')).replace('.0', '').strip()
        
        if not phone or phone.lower() == 'nan' or not ext_num or ext_num.lower() == 'nan':
            continue

        if phone.startswith('61') and len(phone) >= 11:
            phone = '+' + phone
        phone_clean = phone.replace('+', '')

        number_id = phone_map.get(phone) or phone_map.get(phone_clean)
        if not number_id:
            logs.append(f"❌ Row {index+2}: Phone Number {phone} not found in the account inventory.")
            continue

        ext_id = ext_map.get(ext_num)
        if not ext_id:
            logs.append(f"❌ Row {index+2}: Extension Number {ext_num} not found in the account.")
            continue

        # Replicate the exact functional request payload captured from the portal trace log
        payload = {
            "numbers": [
                {
                    "phoneId": int(number_id),
                    "targetPhoneType": "VoiceFax",
                    "targetBillingCodeID": 0,
                    "targetMailbox": int(ext_id),
                    "integrationProviderId": 0,
                    "rcxSubAccountId": ""
                }
            ],
            "controlSum": None,
            "opportunityId": "EMPTY_OPPORTUNITY_ID"
        }

        try:
            res = requests.post(endpoint_url, headers=headers, json=payload, timeout=20)
            status_code = res.status_code
            
            if res.ok:
                res_data = res.json()
                status_obj = res_data.get('status', {})
                
                if status_obj.get('success') is True or res_data.get('billingStatus') == 'Success':
                    logs.append(f"✅ Successfully assigned {phone} to Extension {ext_num} (via Service Web Portal API).")
                else:
                    err_msg = status_obj.get('message') or status_obj.get('errorCode') or "Billing Transaction Denied"
                    logs.append(f"❌ Failed to assign {phone}: {err_msg}")
            else:
                if status_code == 429:
                    logs.append(f"❌ Failed to assign {phone}: Rate limit hit. Retrying batch recommended.")
                    time.sleep(2)
                else:
                    logs.append(f"❌ Failed to assign {phone} (HTTP {status_code}): {res.text}")
                    
        except Exception as e:
            logs.append(f"❌ Error assigning {phone}: {str(e)}")
            
        time.sleep(0.7) # Safety delay window

    if not logs:
        logs.append("No entries were detected inside the execution array.")
        
    return logs
