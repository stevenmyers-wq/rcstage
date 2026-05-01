import pandas as pd
import re
import io
import os
from google import genai
from google.genai import types
from webapp.rc_api import rc_api_call

# --- Formatters & Helpers ---
def normalize_number(txt):
    if pd.isna(txt): return ""
    raw = re.sub(r'[^0-9]', '', str(txt))
    if not raw: return ""
    if raw.startswith("61") and len(raw) >= 11: raw = raw[2:]
    if raw.startswith("0"): raw = raw[1:]
    return raw

def name_match(name1, name2):
    """Bulletproof name matching: handles typos like 'Vos' vs 'Voss' and formatting differences."""
    if not name1 or not name2: return False
    s1 = re.sub(r'[^a-z0-9]', '', str(name1).lower())
    s2 = re.sub(r'[^a-z0-9]', '', str(name2).lower())
    if not s1 or not s2: return False
    
    # Direct substring match (catches allanvos inside allanvoss)
    if s1 in s2 or s2 in s1: return True
    
    # Token subset match
    set1 = set(re.sub(r'[^a-z0-9\s]', ' ', str(name1).lower()).split())
    set2 = set(re.sub(r'[^a-z0-9\s]', ' ', str(name2).lower()).split())
    return set1.issubset(set2) or set2.issubset(set1)

def extract_digits_collection(txt):
    """Safely extracts all distinct phone numbers from a single cell."""
    if pd.isna(txt): return []
    txt_str = str(txt)
    parts = re.split(r'[,\n\r/|;]+', txt_str)
    res = []
    for p in parts:
        clean = re.sub(r'[^0-9]', '', p)
        if clean: res.append(clean)
    return res

def format_e164(val):
    if pd.isna(val) or not str(val).strip(): return "SCP storage"
    raw = re.sub(r'[^0-9]', '', str(val))
    if not raw: return "SCP storage"
    if raw.startswith("0"): raw = raw[1:]
    if raw.startswith("61") and len(raw) >= 11: return "+" + raw
    return "+61" + raw

def clean_ext_num(val):
    if pd.isna(val): return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    if s.endswith('.0'): s = s[:-2]
    return s

def safe_str(val):
    if pd.isna(val): return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    return s

def find_col_index(df, possible_names):
    """Searches prioritizing EXACT matches first."""
    for name in possible_names:
        name_clean = name.strip().lower()
        for i in range(min(15, len(df))):
            for j in range(len(df.columns)):
                val = str(df.iloc[i, j]).strip().lower()
                if val == name_clean: return i, j
    for name in possible_names:
        name_clean = name.strip().lower()
        for i in range(min(15, len(df))):
            for j in range(len(df.columns)):
                val = str(df.iloc[i, j]).strip().lower()
                if name_clean in val: return i, j
    return None, None

def load_sheet_by_name(brd, target_names):
    target_names_lower = [t.lower() for t in target_names]
    for name in brd.sheet_names:
        if name.strip().lower() in target_names_lower:
            return pd.read_excel(brd, sheet_name=name, header=None)
    return pd.DataFrame()

# --- Live API Fetch (V2 Enforced) ---
def fetch_api_data():
    """Fetches Extensions to guarantee we have the Name/Ext Number, then maps Phone Numbers using V2."""
    
    # 1. Fetch all extensions to build a master lookup dictionary
    exts = []
    page = 1
    while True:
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000, 'page': page})
        if not resp or 'records' not in resp: break
        exts.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): break
        page += 1
        
    ext_dict = {str(e['id']): e for e in exts if 'id' in e}
    
    # 2. Fetch all phone numbers strictly using V2
    phones = []
    page = 1
    try:
        while True:
            resp = rc_api_call('/restapi/v2/accounts/~/phone-numbers', params={'perPage': 1000, 'page': page}, raise_error=True)
            if not resp or 'records' not in resp: break
            phones.extend(resp['records'])
            if not resp.get('navigation', {}).get('nextPage'): break
            page += 1
    except Exception as e:
        print("V2 API failed, falling back to V1:", e)
        page = 1
        while True:
            resp = rc_api_call('/restapi/v1.0/account/~/phone-number', params={'perPage': 1000, 'page': page})
            if not resp or 'records' not in resp: break
            phones.extend(resp['records'])
            if not resp.get('navigation', {}).get('nextPage'): break
            page += 1
        
    enriched_phones = []
    for p in phones:
        assignee = p.get('assignee') or p.get('extension') or {}
        ext_id = str(assignee.get('id', ''))
        
        name = str(assignee.get('name', '')).strip()
        num = str(assignee.get('extensionNumber', '')).strip()
        
        # Enrich sparse phone records using the master extension dictionary
        if ext_id and ext_id in ext_dict:
            master = ext_dict[ext_id]
            name = name or str(master.get('name', '')).strip()
            num = num or str(master.get('extensionNumber', '')).strip()
            
        enriched_phones.append({
            'phoneNumber': p.get('phoneNumber'),
            'usageType': p.get('usageType', 'Unknown'),
            'extensionName': name,
            'extensionNumber': num
        })
        
    return enriched_phones

# --- AI Extraction ---
def extract_loa_numbers_with_gemini(pdf_bytes):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: raise ValueError("GEMINI_API_KEY is not set.")
    
    client = genai.Client(api_key=api_key)
    prompt = """
    Extract Data: Analyze the table in this document to extract "Telephone Number Begin" and "Telephone Number End" columns.
    Process Rows:
    - If "Begin" and "End" match, treat as a single number.
    - If "Begin" ends in 00 and "End" ends in 99, treat as a 100-number range and expand into 100 individual consecutive rows.
    Format Numbers:
    - Rule A (Numbers starting with 0): Remove the leading 0 and prefix with 61 (e.g., 02... becomes 612...).
    - Rule B (1300/1800 numbers): Prefix these numbers with 61 (e.g., 1300... becomes 611300...).
    Output Requirements:
    - Provide a CSV block with the header "Port In Number".
    - Sort Order: List all single numbers first, followed by the expanded ranges.
    - Separation: Insert a single empty line between each original data segment to maintain clear grouping.
    - Return ONLY the CSV data, no markdown formatting or extra text.
    """
    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf')
    response = client.models.generate_content(model='gemini-2.5-flash', contents=[pdf_part, prompt])
    return response.text.strip()

# --- Main Processor ---
def process_port_mapping(loa_bytes, brd_bytes):
    # 1. AI Extraction of LOA
    extracted_csv = extract_loa_numbers_with_gemini(loa_bytes)
    loa_numbers = set()
    try:
        loa_df = pd.read_csv(io.StringIO(extracted_csv))
        for col in loa_df.columns:
            for val in loa_df[col]:
                num = normalize_number(val)
                if num: loa_numbers.add(num)
    except: pass
    
    # 2. Fetch Enriched Live Numbers
    api_numbers = fetch_api_data()
    ext_num_to_phones = {}
    ext_name_to_phones = {}
    
    for record in api_numbers:
        ext_num = clean_ext_num(record.get('extensionNumber', ''))
        ext_name = str(record.get('extensionName', '')).strip()
        phone = normalize_number(record.get('phoneNumber'))
        
        if phone:
            if ext_num: ext_num_to_phones.setdefault(ext_num, []).append(phone)
            if ext_name: ext_name_to_phones.setdefault(ext_name, []).append(phone)

    # 3. Universal BRD Parsing
    brd = pd.ExcelFile(io.BytesIO(brd_bytes))
    port_data = {} 
    
    # Process Fallback Sheet First (NUMBERS DUMP)
    phone_to_assigned = {}
    phone_to_ext_num = {}
    dump_df = load_sheet_by_name(brd, ['all phone numbers', 'numbers dump', 'numbers'])
    
    if not dump_df.empty:
        dump_p_r, dump_p_c = find_col_index(dump_df, ['phonenumber', 'phone number'])
        dump_a_r, dump_a_c = find_col_index(dump_df, ['assigned to'])
        n_r, n_c = find_col_index(dump_df, ['name', 'extension name'])
        e_r, e_c = find_col_index(dump_df, ['extension #', 'extension'])
        
        if dump_p_c is not None:
            valid_rows = [r for r in [dump_p_r, dump_a_r, n_r, e_r] if r is not None]
            start_row = max(valid_rows) + 1 if valid_rows else 1
            
            for i in range(start_row, len(dump_df)):
                phone = normalize_number(dump_df.iloc[i, dump_p_c])
                if not phone: continue
                
                assigned = safe_str(dump_df.iloc[i, dump_a_c]) if dump_a_c is not None else ""
                name_val = safe_str(dump_df.iloc[i, n_c]) if n_c is not None else ""
                
                final_name = assigned if assigned else name_val
                if final_name: phone_to_assigned[phone] = final_name
                    
                ext_num = clean_ext_num(dump_df.iloc[i, e_c]) if e_c is not None else ""
                if ext_num: phone_to_ext_num[phone] = ext_num

    # Process Standard Port Mapping sheets (Users, Queues, IVR)
    def extract_from_sheet(df, port_cols, name_cols, ext_cols, temp_cols=None):
        if df.empty: return
        
        port_r, port_c = find_col_index(df, port_cols)
        if port_c is None: return 
        
        temp_r, temp_c = find_col_index(df, temp_cols) if temp_cols else (None, None)
        ext_r, ext_c = find_col_index(df, ext_cols)
        
        if isinstance(name_cols[0], list): 
            fn_r, fn_c = find_col_index(df, name_cols[0])
            ln_r, ln_c = find_col_index(df, name_cols[1])
            name_r = max([r for r in [fn_r, ln_r] if r is not None] or [0])
            
            if fn_c is not None:
                start_row = max([r for r in [port_r, name_r, ext_r] if r is not None] or [0]) + 1
                for i in range(start_row, len(df)):
                    port_list = extract_digits_collection(df.iloc[i, port_c])
                    if not port_list: continue

                    temp_list = extract_digits_collection(df.iloc[i, temp_c]) if temp_c is not None else []
                    fname = safe_str(df.iloc[i, fn_c])
                    lname = safe_str(df.iloc[i, ln_c]) if ln_c is not None else ""
                    target_name = f"{fname} {lname}".strip()
                    ext_num = clean_ext_num(df.iloc[i, ext_c]) if ext_c is not None else ""
                    
                    for idx, num in enumerate(port_list):
                        port = normalize_number(num)
                        if port:
                            temp = temp_list[idx] if idx < len(temp_list) else (temp_list[0] if temp_list else "")
                            if port not in port_data or (temp and not port_data[port]['temp']):
                                port_data[port] = {'name': target_name, 'temp': temp, 'ext': ext_num}
        else:
            n_r, n_c = find_col_index(df, name_cols)
            if n_c is not None:
                start_row = max([r for r in [port_r, n_r, ext_r] if r is not None] or [0]) + 1
                for i in range(start_row, len(df)):
                    port_list = extract_digits_collection(df.iloc[i, port_c])
                    if not port_list: continue

                    temp_list = extract_digits_collection(df.iloc[i, temp_c]) if temp_c is not None else []
                    target_name = safe_str(df.iloc[i, n_c])
                    ext_num = clean_ext_num(df.iloc[i, ext_c]) if ext_c is not None else ""
                    
                    for idx, num in enumerate(port_list):
                        port = normalize_number(num)
                        if port:
                            temp = temp_list[idx] if idx < len(temp_list) else (temp_list[0] if temp_list else "")
                            if port not in port_data or (temp and not port_data[port]['temp']):
                                port_data[port] = {'name': target_name, 'temp': temp, 'ext': ext_num}

    extract_from_sheet(load_sheet_by_name(brd, ['users', 'user']), ['porting number (e164)', 'porting number'], [['first name'], ['last name']], ['extension'])
    extract_from_sheet(load_sheet_by_name(brd, ['call queues', 'queues']), ['phone number'], ['queue name', 'name', 'extension name'], ['extension'], ['temporary number'])
    extract_from_sheet(load_sheet_by_name(brd, ['ivr', 'ivrs']), ['phone number'], ['ivr name', 'menu name', 'name', 'extension name'], ['menu ext', 'extension'], ['temporary number'])

    # 4. Generate Port Mapping Document
    mapping_rows = []
    mapped_temp_numbers = set()
    all_port_in_numbers = set(port_data.keys()).union(loa_numbers)

    for port_in in sorted(list(all_port_in_numbers)):
        data = port_data.get(port_in)
        temp_num = "SCP storage"
        target_name = "Not found in BRD"
        source = "LOA Only" if port_in in loa_numbers else "BRD Only"
        
        if data:
            source = "LOA & BRD" if port_in in loa_numbers else "BRD Only"
            target_name = data['name']
            ext_num = data['ext']
            
            if data['temp']:
                temp_num = format_e164(data['temp'])
            else:
                live_temps = []
                
                # Check Extension Number first (Most accurate)
                if ext_num and ext_num in ext_num_to_phones:
                    live_temps.extend(ext_num_to_phones[ext_num])
                
                # Check Extension Name if no numbers found
                if not live_temps and target_name:
                    for name_key, phones in ext_name_to_phones.items():
                        if name_match(name_key, target_name):
                            live_temps.extend(phones)
                
                # IMPORTANT: Exclude the Port-In number so we capture the true Temporary number
                valid_temps = [p for p in live_temps if p != port_in]
                
                if valid_temps:
                    temp_num = format_e164(valid_temps[0])
                else:
                    # Fallback to BRD Dump (Ext Number)
                    fb_temp = next((p for p, e in phone_to_ext_num.items() if e == ext_num and p != port_in), None) if ext_num else None
                    if fb_temp:
                        temp_num = format_e164(fb_temp)
                    else:
                        # Fallback to BRD Dump (Name)
                        fb_temp = next((p for p, a in phone_to_assigned.items() if name_match(a, target_name) and p != port_in), None) if target_name else None
                        if fb_temp:
                            temp_num = format_e164(fb_temp)

        if temp_num != "SCP storage":
            mapped_temp_numbers.add(normalize_number(temp_num))

        mapping_rows.append({
            "Port In Number": format_e164(port_in),
            "Temporary Number": temp_num,
            "Target Extension Name": target_name,
            "Number Source": source
        })

    port_mapping_df = pd.DataFrame(mapping_rows)

    # 5. Generate Unmapped Numbers enriched by BRD assignment data
    unmapped_rows = []
    seen_unmapped = set()
    
    for record in api_numbers:
        phone = normalize_number(record.get('phoneNumber'))
        
        if phone and phone not in mapped_temp_numbers and phone not in all_port_in_numbers:
            ext_name = str(record.get('extensionName', 'N/A')).strip()
            usage = record.get('usageType', 'Unknown')
            
            # Enrich missing API names (N/A) from the BRD historical dump
            if not ext_name or ext_name.lower() in ['nan', 'n/a', '', 'unknown']:
                ext_name = phone_to_assigned.get(phone, 'N/A')
                
            unmapped_rows.append({
                "Phone Number": format_e164(phone),
                "Associated Object / Extension Name": ext_name,
                "Usage Type": usage
            })
            seen_unmapped.add(phone)

    for phone, name in phone_to_assigned.items():
        if phone not in mapped_temp_numbers and phone not in all_port_in_numbers and phone not in seen_unmapped:
            unmapped_rows.append({
                "Phone Number": format_e164(phone),
                "Associated Object / Extension Name": name,
                "Usage Type": "From BRD Dump Only"
            })
            
    ws_unmapped = pd.DataFrame(unmapped_rows)

    # 6. Output Construction
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        port_mapping_df.to_excel(writer, sheet_name="Port Mapping", index=False)
        
        if not ws_unmapped.empty:
            ws_unmapped.to_excel(writer, sheet_name="Unmapped Numbers In Admin", index=False)
        else:
            pd.DataFrame([["No unmapped numbers found."]]).to_excel(writer, sheet_name="Unmapped Numbers In Admin", index=False, header=False)
        
        try:
            loa_out_df = pd.read_csv(io.StringIO(extracted_csv))
            loa_out_df.to_excel(writer, sheet_name="LOA Raw Extraction", index=False)
        except:
            pd.DataFrame({"AI Extraction Output": [extracted_csv]}).to_excel(writer, sheet_name="LOA Raw Extraction", index=False)
            
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            worksheet.set_column(0, 9, 25)
        
    output.seek(0)
    return output
