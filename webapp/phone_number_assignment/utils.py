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
    Tests the V2 Business Mobile Numbers endpoints you discovered.
    """
    logs = [f"🔍 Testing V2 Business Mobile Endpoints for {phone} -> Ext {ext_num}"]
    
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
    
    # 2. Resolve Ext ID
    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_id = None
    for e in all_exts:
        if str(e.get('extensionNumber', '')).strip() == str(ext_num).strip():
            ext_id = str(e['id'])
            break

    if not ext_id:
        return logs + [f"❌ Extension number {ext_num} not found in account."]

    logs.append(f"ℹ️ Phone ID: {number_id} | Target Ext ID: {ext_id}")
    logs.append("="*60)

    # 3. Test Endpoints based on your schema discovery
    test_endpoints = [
        (
            "V2 Business Mobile (Single PATCH)", 
            f"/restapi/v2/accounts/~/business-mobile-numbers/{number_id}",
            'PATCH',
            {
                "extension": { "id": ext_id }
            }
        ),
        (
            "V2 Business Mobile (Bulk POST)", 
            f"/restapi/v2/accounts/~/business-mobile-numbers",
            'POST',
            {
                "records": [
                    {
                        "id": number_id,
                        "extension": { "id": ext_id }
                    }
                ]
            }
        ),
        (
            "V2 Business Mobile (Bulk PUT)", 
            f"/restapi/v2/accounts/~/business-mobile-numbers",
            'PUT',
            {
                "records": [
                    {
                        "id": number_id,
                        "extension": { "id": ext_id }
                    }
                ]
            }
        ),
        (
            "V2 Business Mobile (Bulk PATCH)", 
            f"/restapi/v2/accounts/~/business-mobile-numbers",
            'PATCH',
            {
                "records": [
                    {
                        "id": number_id,
                        "extension": { "id": ext_id }
                    }
                ]
            }
        )
    ]

    for name, ep, method, payload in test_endpoints:
        logs.append(f"\n▶ Testing: {name} [{method} {ep}]")
        
        try:
            res = rc_api_call(ep, method=method, json=payload, token=token, return_response=True)
            status = getattr(res, 'status_code', 'Unknown')
            err = extract_error(res)
            
            if res and getattr(res, 'ok', False):
                logs.append(f"  ✅ SUCCESS! The API accepted the assignment.")
                # If it's the bulk endpoint, check the bulkItemSuccessful flag in the response
                try:
                    resp_json = res.json()
                    logs.append(f"  Response Body: {resp_json}")
                except:
                    pass
                return logs
            else:
                logs.append(f"  ❌ HTTP {status} - {err}")
                if status == 429: time.sleep(2)
        except Exception as e:
            logs.append(f"  ❌ Exception: {str(e)}")
            
        time.sleep(1)

    logs.append("\n🛑 EXHAUSTED BUSINESS MOBILE ENDPOINTS.")
    return logs

def process_assignments(records, token):
    return ["Batch processing is temporarily disabled while we run the Endpoint Diagnostic Sandbox below."]
