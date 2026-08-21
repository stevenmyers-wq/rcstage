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

def _standard_endpoints(number_id, ext_id):
    """Assignment endpoints for regular (non-mobile) inventory numbers/DIDs.
    The V2 assign endpoint (PATCH /restapi/v2/accounts/~/phone-numbers/{id})
    REQUIRES usageType; DirectNumber is correct when assigning to an extension."""
    assign_body = {"usageType": "DirectNumber", "extension": {"id": ext_id}}
    return [
        ("V2 Assign (PATCH, with usageType)", f"/restapi/v2/accounts/~/phone-numbers/{number_id}", 'PATCH', assign_body),
    ]

def _business_mobile_endpoints(number_id, ext_id):
    """Assignment endpoints for Business Mobile numbers (kept for comparison)."""
    ext_body = {"extension": {"id": ext_id}}
    bulk_body = {"records": [{"id": number_id, "extension": {"id": ext_id}}]}
    return [
        ("V2 Business Mobile (Bulk POST)",    "/restapi/v2/accounts/~/business-mobile-numbers",              'POST',  bulk_body),
        ("V2 Business Mobile (Single PATCH)", f"/restapi/v2/accounts/~/business-mobile-numbers/{number_id}", 'PATCH', ext_body),
        ("V2 Business Mobile (Bulk PUT)",     "/restapi/v2/accounts/~/business-mobile-numbers",              'PUT',   bulk_body),
        ("V2 Business Mobile (Bulk PATCH)",   "/restapi/v2/accounts/~/business-mobile-numbers",              'PATCH', bulk_body),
    ]

def _probe_endpoints(endpoints, token, logs):
    """Fire each candidate endpoint; return True on the first success."""
    for name, ep, method, payload in endpoints:
        logs.append(f"\n▶ Testing: {name} [{method} {ep}]")
        try:
            res = rc_api_call(ep, method=method, json=payload, token=token, return_response=True)
            status = getattr(res, 'status_code', 'Unknown')
            err = extract_error(res)

            if res and getattr(res, 'ok', False):
                logs.append("  ✅ SUCCESS! The API accepted the assignment.")
                try:
                    logs.append(f"  Response Body: {res.json()}")
                except Exception:
                    pass
                return True
            else:
                logs.append(f"  ❌ HTTP {status} - {err}")
                if status == 429:
                    time.sleep(2)
        except Exception as e:
            logs.append(f"  ❌ Exception: {str(e)}")
        time.sleep(1)
    return False

def run_exhaustive_debug(phone, ext_num, token):
    """
    Probes the assignment endpoints for a single number/extension pair.

    Tries the STANDARD (non-mobile) endpoints first — these cover regular
    inventory numbers/DIDs — then falls back to the Business Mobile endpoints
    so the log shows exactly which resource (if any) accepts the assignment.
    """
    logs = [f"🔍 Testing assignment endpoints for {phone} -> Ext {ext_num}"]

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
    num_type = number_data.get('type', 'Unknown')
    usage_type = number_data.get('usageType', 'Unknown')

    # 2. Resolve Ext ID
    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_id = None
    for e in all_exts:
        if str(e.get('extensionNumber', '')).strip() == str(ext_num).strip():
            ext_id = str(e['id'])
            break

    if not ext_id:
        return logs + [f"❌ Extension number {ext_num} not found in account."]

    logs.append(f"ℹ️ Phone ID: {number_id} | Type: {num_type} | Usage: {usage_type} | Target Ext ID: {ext_id}")

    is_mobile = 'mobile' in str(num_type).lower()
    if is_mobile:
        logs.append("ℹ️ Number reports as MOBILE — trying Business Mobile endpoints first.")
        groups = [
            ("BUSINESS MOBILE", _business_mobile_endpoints(number_id, ext_id)),
            ("STANDARD (non-mobile)", _standard_endpoints(number_id, ext_id)),
        ]
    else:
        logs.append("ℹ️ Number is NON-MOBILE — trying Standard endpoints first.")
        groups = [
            ("STANDARD (non-mobile)", _standard_endpoints(number_id, ext_id)),
            ("BUSINESS MOBILE", _business_mobile_endpoints(number_id, ext_id)),
        ]

    for group_name, endpoints in groups:
        logs.append("=" * 60)
        logs.append(f"— {group_name} endpoints —")
        if _probe_endpoints(endpoints, token, logs):
            logs.append(f"\n🎯 Working endpoint found in the {group_name} group. Bulk upload can be wired to it.")
            return logs

    logs.append("\n🛑 EXHAUSTED ALL ENDPOINTS — no resource accepted the assignment.")
    return logs

def _clean_number(value):
    return str(value).replace('+', '').replace(' ', '').replace('-', '').strip()

def _row_value(row, *names):
    """Case/space-insensitive lookup for a column across possible header names."""
    normalised = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        v = normalised.get(name.strip().lower())
        if v is not None and str(v).strip() and str(v).strip().lower() != 'nan':
            return str(v).strip()
    return ''

def process_assignments(records, token):
    """Assign each inventory number to its target extension via the V2 Standard
    endpoint (PATCH /restapi/v2/accounts/~/phone-numbers/{id}) — the same
    proven-mutable path used by the Cost Centres tool. One PATCH per row, with a
    per-row result line."""
    logs = []

    # Build lookups once.
    all_numbers = fetch_all_pages('/restapi/v2/accounts/~/phone-numbers', token)
    number_by_clean = {}
    for n in all_numbers:
        n_phone = n.get('phoneNumber', '')
        if n_phone:
            number_by_clean[_clean_number(n_phone)] = n

    all_exts = fetch_all_pages('/restapi/v1.0/account/~/extension', token)
    ext_id_by_num = {}
    for e in all_exts:
        ext_num = str(e.get('extensionNumber', '')).strip()
        if ext_num:
            ext_id_by_num[ext_num] = str(e['id'])

    success = 0
    failed = 0
    skipped = 0

    for i, row in enumerate(records, start=1):
        phone = _row_value(row, 'Phone Number', 'PhoneNumber', 'Number')
        ext_num = _row_value(row, 'Extension Number', 'ExtensionNumber', 'Extension')

        if not phone and not ext_num:
            continue  # blank row
        if not phone or not ext_num:
            skipped += 1
            logs.append(f"⚠️ Row {i}: skipped — missing {'phone number' if not phone else 'extension number'}.")
            continue

        number_data = number_by_clean.get(_clean_number(phone))
        if not number_data:
            failed += 1
            logs.append(f"❌ Row {i}: {phone} — not found in account inventory.")
            continue

        ext_id = ext_id_by_num.get(ext_num)
        if not ext_id:
            failed += 1
            logs.append(f"❌ Row {i}: extension {ext_num} — not found in account.")
            continue

        number_id = str(number_data.get('id', ''))
        endpoint = f'/restapi/v2/accounts/~/phone-numbers/{number_id}'
        # usageType is REQUIRED by the V2 assign endpoint; DirectNumber is the
        # correct type when assigning an inventory number to an extension.
        payload = {"usageType": "DirectNumber", "extension": {"id": ext_id}}

        try:
            res = rc_api_call(endpoint, method='PATCH', json=payload, token=token, return_response=True)
            status = getattr(res, 'status_code', 'Unknown')
            if res and getattr(res, 'ok', False):
                success += 1
                logs.append(f"✅ Row {i}: {phone} → Ext {ext_num}.")
            else:
                failed += 1
                logs.append(f"❌ Row {i}: {phone} → Ext {ext_num} — HTTP {status} - {extract_error(res)}")
                if status == 429:
                    time.sleep(2)
        except Exception as e:
            failed += 1
            logs.append(f"❌ Row {i}: {phone} → Ext {ext_num} — Exception: {str(e)}")

        time.sleep(0.1)

    summary = f"Done. {success} assigned, {failed} failed, {skipped} skipped ({len(records)} row(s) read)."
    return [summary] + logs
