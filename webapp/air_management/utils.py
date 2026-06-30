# webapp/air_management/utils.py
import pandas as pd
import io
import re
from webapp.rc_api import rc_api_call

def fetch_all_assistants(token=None):
    """Fetch all AIR instances from the account."""
    assistants = []
    page = 1
    while True:
        resp = rc_api_call(f'/ai/iva/v1/accounts/~/assistants?perPage=100&page={page}', token=token, raise_error=False)
        if not resp or 'records' not in resp:
            break
        assistants.extend(resp['records'])
        if not resp.get('paging', {}).get('nextPage'):
            break
        page += 1
    return assistants

def parse_assistant_to_row(assistant):
    """Flatten an Assistant object into an Excel row."""
    sys_settings = assistant.get('systemSettings', {})
    fallback = assistant.get('fallbackExtension', {})
    languages = assistant.get('languages', [])
    
    return {
        'ID (Leave blank for new)': assistant.get('id', ''),
        'Name': assistant.get('name', ''),
        'Extension Number': assistant.get('extensionNumber', ''),
        'Company Name': assistant.get('companyName', ''),
        'Company Description': assistant.get('companyDescription', ''),
        'System Type': sys_settings.get('systemType', 'PBX_VOICE'),
        'Voice Name': sys_settings.get('voiceName', 'Kore'),
        'Languages': ', '.join(languages) if languages else 'en-US',
        'Time Zone': assistant.get('timeZone', ''),
        'Fallback Extension ID': fallback.get('id', ''),
        'Site ID': assistant.get('siteId', ''),
        'Tools Version': assistant.get('toolsVersion', ''),
        'Website': assistant.get('website', ''),
        'Prompt Template': assistant.get('promptTemplate', '')
    }

def clean_ext_num(val):
    """Cleans up floats interpreted from Excel."""
    if pd.isna(val): return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    if s.endswith('.0'): s = s[:-2]
    return s

def build_assistant_payload(row):
    """Build the API payload from an Excel row."""
    payload = {
        "name": str(row.get('Name', '')).strip(),
        "extensionNumber": clean_ext_num(row.get('Extension Number')),
        "companyDescription": str(row.get('Company Description', '')).strip(),
        "systemSettings": {
            "systemType": str(row.get('System Type', 'PBX_VOICE')).strip(),
            "voiceName": str(row.get('Voice Name', 'Kore')).strip()
        }
    }
    
    # Process comma-separated languages array
    langs = str(row.get('Languages', '')).strip()
    if langs and langs.lower() != 'nan':
        payload["languages"] = [l.strip() for l in langs.split(',') if l.strip()]

    # Optional String fields
    company_name = str(row.get('Company Name', '')).strip()
    if company_name and company_name.lower() != 'nan':
        payload["companyName"] = company_name

    tz = str(row.get('Time Zone', '')).strip()
    if tz and tz.lower() != 'nan':
        payload["timeZone"] = tz
        
    site_id = clean_ext_num(row.get('Site ID'))
    if site_id:
        payload["siteId"] = site_id

    website = str(row.get('Website', '')).strip()
    if website and website.lower() != 'nan':
        payload["website"] = website
        
    prompt = str(row.get('Prompt Template', '')).strip()
    if prompt and prompt.lower() != 'nan':
        payload["promptTemplate"] = prompt
        
    tools = str(row.get('Tools Version', '')).strip()
    if tools and tools.lower() != 'nan':
        payload["toolsVersion"] = tools

    # Fallback extension
    fallback_id = clean_ext_num(row.get('Fallback Extension ID'))
    if fallback_id:
        payload["fallbackExtension"] = {"id": fallback_id}
        
    return payload
