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
    raw_text = getattr(res, 'text', '')
    try:
        err_json = res.json() if res else {}
        if isinstance(err_json, dict):
            if err_json.get('errors'):
                return " | ".join([f"[{e.get('errorCode', 'ERR')}] {e.get('message', str(e))}" for e in err_json.get('errors', [])])
            elif err_json.get('message'):
                return err_json.get('message')
    except Exception:
        pass
    return raw_text.strip() if raw_text.strip() else "Unknown Error"

def process_assignments(records, token):
    # Map phone string to internal numerical ID
    all_numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    phone_map = {}
    
    for n in all_numbers:
        if n.get('phoneNumber'):
            phone_num = n['phoneNumber'].strip()
            phone_map[phone_num] = n
            phone_map[phone_num.replace('+', '')] = n

    # Map Extension short Number to system internal long ID
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

        if phone.startswith('61') and len(phone) >= 11:
            phone = '+' + phone
        phone_clean = phone.replace('+', '')

        number_data = phone_map.get(phone) or phone_map.get(phone_clean)
        if not number_data:
            logs.append(f"❌ Row {index+2}: Phone Number {phone} not found in the account inventory.")
            continue

        ext_id = ext_map.get(ext_num)
        if not ext_id:
            logs.append(f"❌ Row {index+2}: Extension Number {ext_num} not found in the account.")
            continue

        number_id = str(number_data.get('id', ''))
        payment_type = str(number_data.get('paymentType', ''))
        usage_type = str(number_data.get('usageType', ''))
        
        # Check if the number is categorized as a Mobile line
        is_mobile = 'Mobile' in payment_type or 'Mobile' in usage_type
        
        if is_mobile:
            # Use the specialized V2 Business Mobile Numbers Bulk API
            endpoint = '/restapi/v2/accounts/~/business-mobile-numbers'
            payload = {
                "records": [
                    {
                        "id": number_id,
                        "extension": { "id": ext_id }
                    }
                ]
            }
            try:
                res = rc_api_call(endpoint, method='POST', json=payload, token=token, return_response=True)
                if res and getattr(res, 'ok', False):
                    res_data = res.json()
                    item_data = res_data.get('records', [{}])[0]
                    
                    if item_data.get('bulkItemSuccessful') is True:
                        logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (via Business Mobile API).")
                    else:
                        errors = item_data.get('bulkItemErrors', [])
                        err_msg = " | ".join([f"[{e.get('errorCode', 'ERR')}] {e.get('message', str(e))}" for e in errors])
                        logs.append(f"❌ Failed to assign {phone}: {err_msg}")
                else:
                    err_msg = extract_error(res)
                    logs.append(f"❌ Failed to assign {phone} (HTTP {getattr(res, 'status_code', 'Unknown')}): {err_msg}")
            except Exception as e:
                logs.append(f"❌ Error assigning {phone}: {str(e)}")
                
        else:
            # Standard Voice DIDs and Toll-Free Numbers
            endpoint = f'/restapi/v2/accounts/~/phone-numbers/{number_id}'
            payload = {
                "usageType": "DirectNumber",
                "extension": { "id": ext_id }
            }
            try:
                res = rc_api_call(endpoint, method='PATCH', json=payload, token=token, return_response=True)
                if res and getattr(res, 'ok', False):
                    logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num}.")
                else:
                    # Fallback without usageType constraint
                    time.sleep(0.5)
                    fb_payload = { "extension": { "id": ext_id } }
                    res_fb = rc_api_call(endpoint, method='PATCH', json=fb_payload, token=token, return_response=True)
                    if res_fb and getattr(res_fb, 'ok', False):
                        logs.append(f"✅ Successfully assigned {phone} to Ext {ext_num} (Native Fallback).")
                    else:
                        err_msg = extract_error(res_fb)
                        logs.append(f"❌ Failed to assign {phone} (HTTP {getattr(res_fb, 'status_code', 'Unknown')}): {err_msg}")
            except Exception as e:
                logs.append(f"❌ Error assigning {phone}: {str(e)}")
                
        time.sleep(0.7) # Safety delay window

    if not logs:
        logs.append("No entries were detected inside the execution array.")
        
    return logs
