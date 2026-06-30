# webapp/air_management/utils.py
import pandas as pd
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

def fetch_skill_details(skill_id, token=None):
    """Fetch the specific details of a skill."""
    return rc_api_call(f'/ai/iva/v1/accounts/~/skills/{skill_id}', token=token, raise_error=False)

def parse_assistant_to_row(assistant, token=None):
    """Flatten an Assistant object and its active Skills into an Excel row."""
    sys_settings = assistant.get('systemSettings', {})
    fallback = assistant.get('fallbackExtension', {})
    languages = assistant.get('languages', [])
    idle_rule = assistant.get('idleCallerRule', {})
    bh_action = idle_rule.get('businessHours', {})
    ah_action = idle_rule.get('closedHours', {})
    
    row = {
        'AIR ID (Leave blank for new)': assistant.get('id', ''),
        'Name': assistant.get('name', ''),
        'Extension Number': assistant.get('extensionNumber', ''),
        'Company Description': assistant.get('companyDescription', ''),
        'System Type': sys_settings.get('systemType', 'PBX_VOICE'),
        'Voice Name': sys_settings.get('voiceName', 'Kore'),
        'Languages': ', '.join(languages) if languages else 'en-US',
        'Fallback Extension ID': fallback.get('id', ''),
        'Site ID': assistant.get('siteId', ''),
        'Website': assistant.get('website', ''),
        'Prompt Template': assistant.get('promptTemplate', ''),
        'Idle Action (BH)': bh_action.get('actionType', ''),
        'Idle Target (BH)': bh_action.get('extension', {}).get('id', ''),
        'Idle Action (AH)': ah_action.get('actionType', ''),
        'Idle Target (AH)': ah_action.get('extension', {}).get('id', ''),
        'Greeting (BH Text)': '',
        'Greeting (AH Text)': '',
        'Knowledge Base IDs': '',
        'Routing Rule 1': '', 'Routing Target 1': '',
        'Routing Rule 2': '', 'Routing Target 2': '',
        'Routing Rule 3': '', 'Routing Target 3': ''
    }

    # Fetch and map active skills
    if 'skills' in assistant and token:
        for s_stub in assistant['skills']:
            skill_detail = fetch_skill_details(s_stub['id'], token)
            if not skill_detail or 'skill' not in skill_detail: continue
            
            skill = skill_detail['skill']
            stype = skill.get('skillType')
            
            if stype == 'GREETING':
                row['Greeting (BH Text)'] = skill.get('businessHours', {}).get('text', '')
                row['Greeting (AH Text)'] = skill.get('closedHours', {}).get('text', '')
            
            elif stype == 'KNOWLEDGE_BASE':
                contexts = skill.get('contexts', [])
                row['Knowledge Base IDs'] = ', '.join([c.get('contextId', '') for c in contexts])
                
            elif stype == 'CALL_ROUTING':
                rules = skill.get('rules', [])
                for i, r in enumerate(rules[:3]): # Map up to 3 routing rules
                    row[f'Routing Rule {i+1}'] = r.get('rule', '')
                    target = r.get('externalNumber', '')
                    if not target and r.get('extension'):
                        target = r['extension'].get('id', '')
                    row[f'Routing Target {i+1}'] = target

    return row

def clean_ext_num(val):
    if pd.isna(val): return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    if s.endswith('.0'): s = s[:-2]
    return s

def build_assistant_payload(row):
    """Build the core API payload for the base Assistant."""
    payload = {
        "name": str(row.get('Name', '')).strip(),
        "extensionNumber": clean_ext_num(row.get('Extension Number')),
        "companyDescription": str(row.get('Company Description', '')).strip(),
        "systemSettings": {
            "systemType": str(row.get('System Type', 'PBX_VOICE')).strip(),
            "voiceName": str(row.get('Voice Name', 'Kore')).strip()
        }
    }
    
    langs = str(row.get('Languages', '')).strip()
    if langs and langs.lower() != 'nan':
        payload["languages"] = [l.strip() for l in langs.split(',') if l.strip()]

    def add_str(field, key):
        val = str(row.get(field, '')).strip()
        if val and val.lower() != 'nan': payload[key] = val

    add_str('Site ID', 'siteId')
    add_str('Prompt Template', 'promptTemplate')
    add_str('Website', 'website')

    fallback_id = clean_ext_num(row.get('Fallback Extension ID'))
    if fallback_id:
        payload["fallbackExtension"] = {"id": fallback_id}
        
    # Idle Caller Rules
    bh_act = str(row.get('Idle Action (BH)', '')).strip()
    bh_tgt = clean_ext_num(row.get('Idle Target (BH)'))
    ah_act = str(row.get('Idle Action (AH)', '')).strip()
    ah_tgt = clean_ext_num(row.get('Idle Target (AH)'))

    if bh_act or ah_act:
        idle_rule = {}
        bh_final = bh_act if bh_act else 'Disconnect'
        idle_rule['businessHours'] = {'actionType': bh_final}
        if bh_final == 'Extension' and bh_tgt: idle_rule['businessHours']['extension'] = {'id': bh_tgt}
            
        ah_final = ah_act if ah_act else 'Disconnect'
        idle_rule['closedHours'] = {'actionType': ah_final}
        if ah_final == 'Extension' and ah_tgt: idle_rule['closedHours']['extension'] = {'id': ah_tgt}
            
        payload['idleCallerRule'] = idle_rule
        
    return payload

def build_skills_payloads(row):
    """Parses the Excel row and returns a dictionary of SkillOption payloads to apply."""
    skills = {}

    # 1. GREETING
    bh_greet = str(row.get('Greeting (BH Text)', '')).strip()
    ah_greet = str(row.get('Greeting (AH Text)', '')).strip()
    if (bh_greet and bh_greet.lower() != 'nan') or (ah_greet and ah_greet.lower() != 'nan'):
        greet_payload = {"skillType": "GREETING"}
        if bh_greet and bh_greet.lower() != 'nan': greet_payload["businessHours"] = {"text": bh_greet}
        if ah_greet and ah_greet.lower() != 'nan': greet_payload["closedHours"] = {"text": ah_greet}
        skills["GREETING"] = greet_payload

    # 2. KNOWLEDGE BASE
    kb_str = str(row.get('Knowledge Base IDs', '')).strip()
    if kb_str and kb_str.lower() != 'nan':
        contexts = [{"contextId": k.strip()} for k in kb_str.split(',') if k.strip()]
        if contexts:
            skills["KNOWLEDGE_BASE"] = {"skillType": "KNOWLEDGE_BASE", "contexts": contexts}

    # 3. CALL ROUTING (Transfer by Context)
    rules = []
    for i in range(1, 4):
        rule_text = str(row.get(f'Routing Rule {i}', '')).strip()
        target = clean_ext_num(row.get(f'Routing Target {i}'))
        if rule_text and rule_text.lower() != 'nan' and target and target.lower() != 'nan':
            rule_obj = {"rule": rule_text, "disabled": False}
            if target.startswith('+') or len(target) >= 10:
                rule_obj["externalNumber"] = target
            else:
                rule_obj["extension"] = {"id": target}
            rules.append(rule_obj)
    
    if rules:
        skills["CALL_ROUTING"] = {"skillType": "CALL_ROUTING", "rules": rules}

    return skills
