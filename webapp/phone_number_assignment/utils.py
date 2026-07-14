import io
import time
import pandas as pd
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
        # Check if it has no extension assigned
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

        # Auto-adjust column widths
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                sheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
                
    output.seek(0)
    return output

def extract_error(res):
    """Safely extracts the deepest error message from a RingCentral API response."""
    raw_text = getattr(res, 'text', '')
    err_msg = ""
    try:
        err_json = res.json() if res else {}
        if isinstance(err_json, dict):
            if err_json.get('errors'):
                err_msg = " | ".join([e.get('message', str(e)) for e in err_json.get('errors', [])])
            elif err_json.get('message'):
                err_msg = err_json.get('message')
    except Exception:
        pass
    
    return err_msg if err_msg else (raw_text.strip() if raw_text.strip() else "Empty/Unknown Response")

def process_assignments(records, token):
    # Map phone string to ID using the v2 API
    all_numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    phone_map = {}
    
    for n in all_numbers:
        if n.get('phoneNumber'):
            phone_num = n['phoneNumber'].strip()
            num_id = str(n.get('id', ''))
            phone_map[phone_num] = num_id
            phone_map[phone_num.replace('+', '')] = num_id

    # Map Extension Number to Extension ID
    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_map = {}
    for e in all_exts:
        if e.get('extensionNumber'):
            ext_map[str(e['extensionNumber']).strip()] = str(e['id'])

    logs = []
    
    for index, row in enumerate(records):
        phone = str(row.get('Phone Number', '')).strip()
        ext_num = str(row.get('Extension Number', '')).replace('.0', '').strip()
        
        if not phone or phone.lower() == 'nan' or not ext_num or ext_num.lower() == 'nan':
            continue

        # Normalise phone if missing the +
        if phone.startswith('61') and len(phone) >= 11:
            phone = '+' + phone
            
        phone_clean = phone.replace('+', '')

        number_id = phone_map.get(phone) or phone_map.get(phone_clean)
        if not number_id:
            logs.append(f"❌ Row {index+2}: Phone Number {phone} not found in the account.")
            continue

        ext_id = ext_map.get(ext_num)
        if not ext_id:
            logs.append(f"❌ Row {index+2}: Extension Number {ext_num} not found in the account.")
            continue

        # Endpoints
        ep_v1 = f'/restapi/v1.0/account/~/phone-number/{number_id}'
        ep_v2 = f'/restapi/v2/accounts/~/phone-numbers/{number_id}'
        
        # Payloads
        payload_no_usage = { "extension": { "id": ext_id } }
        payload_direct = { "usageType": "DirectNumber", "extension": { "id": ext_id } }

        try:
            # ATTEMPT 1: V1 API - No Usage Type (Best for Mobile/SMS Numbers)
            res1 = rc_api_call(ep_v1, method='PUT', json=payload_no_usage, token=token, return_response=True)
            if res1 and getattr(res1, 'ok', False):
                logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (Native Type).")
                continue
            
            # ATTEMPT 2: V1 API - Explicit DirectNumber
            time.sleep(0.5)
            res2 = rc_api_call(ep_v1, method='PUT', json=payload_direct, token=token, return_response=True)
            if res2 and getattr(res2, 'ok', False):
                logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (DirectNumber).")
                continue

            # ATTEMPT 3: V2 API - No Usage Type
            time.sleep(0.5)
            res3 = rc_api_call(ep_v2, method='PATCH', json=payload_no_usage, token=token, return_response=True)
            if res3 and getattr(res3, 'ok', False):
                logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (V2 Native).")
                continue

            # ATTEMPT 4: V2 API - Explicit DirectNumber
            time.sleep(0.5)
            res4 = rc_api_call(ep_v2, method='PATCH', json=payload_direct, token=token, return_response=True)
            if res4 and getattr(res4, 'ok', False):
                logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (V2 DirectNumber).")
                continue

            # If all attempts failed, report the V1 No-Usage error as it is usually the most accurate
            final_err = extract_error(res1)
            status_code = getattr(res1, 'status_code', 'Unknown')
            
            if status_code == 429:
                logs.append(f"❌ Failed to assign {phone}: Rate limit hit. Try again.")
            else:
                logs.append(f"❌ Failed to assign {phone} (HTTP {status_code}): {final_err}")

        except Exception as e:
            logs.append(f"❌ Error assigning {phone}: {str(e)}")
            
        time.sleep(0.6) # Pace to avoid hitting RC limits rapidly

    if not logs:
        logs.append("No valid records found to process. Please ensure 'Phone Number' and 'Extension Number' columns are populated.")
        
    return logs
