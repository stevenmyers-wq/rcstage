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

def extract_error(res):
    raw_text = getattr(res, 'text', '')
    try:
        err_json = res.json() if res else {}
        if isinstance(err_json, dict):
            code = err_json.get('errorCode', '')
            if not code and err_json.get('errors') and len(err_json['errors']) > 0:
                code = err_json['errors'][0].get('errorCode', '')
            
            if err_json.get('errors'):
                err_msg = " | ".join([e.get('message', str(e)) for e in err_json.get('errors', [])])
            else:
                err_msg = err_json.get('message', '')
                
            if code:
                return f"[{code}] {err_msg}"
            return err_msg
    except Exception:
        pass
    return raw_text.strip() if raw_text.strip() else "Empty/Unknown Response"

def run_exhaustive_debug(phone, ext_num, token):
    """
    Exhaustive brute force diagnostic to find exactly which schema RingCentral accepts
    for SMS/Mobile numbers based on the user-provided enums.
    """
    logs = [f"🔍 Starting Exhaustive Debug for {phone} -> Ext {ext_num}"]
    
    # 1. Resolve Number ID
    all_numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    number_data = None
    clean_target = phone.replace('+', '').strip()
    
    for n in all_numbers:
        n_phone = n.get('phoneNumber', '')
        if n_phone and clean_target in n_phone.replace('+', ''):
            number_data = n
            break

    if not number_data:
        return logs + [f"❌ Phone number {phone} not found in account."]

    number_id = str(number_data.get('id', ''))
    current_usage = number_data.get('usageType', 'Unknown')
    payment_type = number_data.get('paymentType', 'Unknown')
    
    # 2. Resolve Ext ID
    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_id = None
    for e in all_exts:
        if str(e.get('extensionNumber', '')).strip() == str(ext_num).strip():
            ext_id = str(e['id'])
            break

    if not ext_id:
        return logs + [f"❌ Extension number {ext_num} not found in account."]

    logs.append(f"ℹ️ Phone ID: {number_id} | PaymentType: {payment_type} | Current UsageType: {current_usage} | Target Ext ID: {ext_id}")
    logs.append("="*60)

    # 3. List of schemas to test (including omitting it entirely)
    all_schemas = [
        "OMITTED",
        "MainCompanyNumber", "DirectNumber", "Inventory", "InventoryPartnerBusinessMobileNumber", 
        "InventoryFmcBusinessMobileNumber", "PartnerBusinessMobileNumber", "AdditionalCompanyNumber", 
        "CompanyNumber", "PhoneLine", "CompanyFaxNumber", "ForwardedNumber", "ForwardedCompanyNumber", 
        "ContactCenterNumber", "ConferencingNumber", "MeetingsNumber", "NumberStorage", 
        "BusinessMobileNumber", "FmcBusinessMobileNumber", "ELIN", "InventoryMobileNumber"
    ]

    ep_v1 = f'/restapi/v1.0/account/~/phone-number/{number_id}'
    ep_v2 = f'/restapi/v2/accounts/~/phone-numbers/{number_id}'

    for usage in all_schemas:
        logs.append(f"\n▶ Testing schema: {usage}")
        payload = { "extension": { "id": ext_id } }
        if usage != "OMITTED":
            payload["usageType"] = usage

        # V2 Attempt
        res_v2 = rc_api_call(ep_v2, method='PATCH', json=payload, token=token, return_response=True)
        status_v2 = getattr(res_v2, 'status_code', 'Unknown')
        err_v2 = extract_error(res_v2)
        
        if res_v2 and getattr(res_v2, 'ok', False):
            logs.append(f"  ✅ V2 SUCCESS! The API accepted {usage}.")
            logs.append(f"  (Testing halted as number is now assigned)")
            return logs
        else:
            logs.append(f"  ❌ V2: HTTP {status_v2} - {err_v2}")
            if status_v2 == 429: time.sleep(2)
            
        time.sleep(0.5)

        # V1 Attempt
        res_v1 = rc_api_call(ep_v1, method='PUT', json=payload, token=token, return_response=True)
        status_v1 = getattr(res_v1, 'status_code', 'Unknown')
        err_v1 = extract_error(res_v1)
        
        if res_v1 and getattr(res_v1, 'ok', False):
            logs.append(f"  ✅ V1 SUCCESS! The API accepted {usage}.")
            logs.append(f"  (Testing halted as number is now assigned)")
            return logs
        else:
            logs.append(f"  ❌ V1: HTTP {status_v1} - {err_v1}")
            if status_v1 == 429: time.sleep(2)

        time.sleep(0.5)

    logs.append("\n🛑 EXHAUSTED ALL SCHEMAS. None of the payloads worked.")
    return logs

def process_assignments(records, token):
    """The standard batch processing function, kept intact for when the debug reveals the solution."""
    return ["Batch processing is temporarily disabled while you run the Exhaustive Diagnostic Sandbox below."]
