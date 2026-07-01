import pandas as pd
from webapp.rc_api import rc_api_call

def fetch_all_assistants():
    """Fetch all AIR instances from the account."""
    assistants = []
    page = 1
    while True:
        resp = rc_api_call(f'/ai/iva/v1/accounts/~/assistants?perPage=100&page={page}', raise_error=False)
        if not resp or 'records' not in resp:
            break
        assistants.extend(resp['records'])
        if not resp.get('paging', {}).get('nextPage'):
            break
        page += 1
    return assistants

def fetch_skill_details(skill_id):
    """Fetch the specific details of a skill."""
    return rc_api_call(f'/ai/iva/v1/accounts/~/skills/{skill_id}', raise_error=False)

def parse_assistant_to_row(assistant):
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
        'Business Hours Schedule': '',
        'Booking Link': '',
        'Sync Directory (Yes/No)': '',
        'Directory Restricted Ext IDs': '',
        'Knowledge Base IDs': '',
        'FAQ 1 Question': '', 'FAQ 1 Answer': '',
        'FAQ 2 Question': '', 'FAQ 2 Answer': '',
        'FAQ 3 Question': '', 'FAQ 3 Answer': ''
    }

    # Initialize columns for up to 10 context rules
    for i in range(1, 11):
        row[f'Context {i} Rule'] = ''
        row[f'Context {i} Target'] = ''
        row[f'Context {i} Disabled (Yes/No)'] = ''

    # Fetch and map active skills
    if 'skills' in assistant:
        for s_stub in assistant['skills']:
            skill_detail = fetch_skill_details(s_stub['id'])
            if not skill_detail or 'skill' not in skill_detail: continue
            
            skill = skill_detail['skill']
            stype = skill.get('skillType')
            
            if stype == 'GREETING':
                row['Greeting (BH Text)'] = skill.get('businessHours', {}).get('text', '')
                row['Greeting (AH Text)'] = skill.get('closedHours', {}).get('text', '')
            
            elif stype == 'LOCATION_AND_BH':
                row['Business Hours Schedule'] = 'Configured (Requires Manual Update if Changing)'
                
            elif stype == 'BOOKING':
                row['Booking Link'] = skill.get('link', '')
                
            elif stype == 'CONTACT_DIRECTORY':
                row['Sync Directory (Yes/No)'] = 'Yes' if skill.get('syncDialByNameDirectory') else 'No'
                restricted = skill.get('restrictedExtensions', [])
                row['Directory Restricted Ext IDs'] = ', '.join([str(e.get('id', '')) for e in restricted])
                
            elif stype == 'QA':
                corpus = skill.get('corpus', [])
                for i, qa in enumerate(corpus[:3]): 
                    row[f'FAQ {i+1} Question'] = qa.get('question', '')
                    row[f'FAQ {i+1} Answer'] = qa.get('answer', '')

            elif stype == 'KNOWLEDGE_BASE':
                contexts = skill.get('contexts', [])
                row['Knowledge Base IDs'] = ', '.join([c.get('contextId', '') for c in contexts])
                
            elif stype == 'CALL_ROUTING':
                rules = skill.get('rules', [])
                for i, r in enumerate(rules[:10]): 
                    row[f'Context {i+1} Rule'] = r.get('rule', '')
                    row[f'Context {i+1} Disabled (Yes/No)'] = 'Yes' if r.get('disabled') else 'No'
                    
                    target = r.get('externalNumber', '')
                    if not target and r.get('extension'):
                        target = r['extension'].get('id', '')
                    if not target and r.get('contactCenterNumber'):
                        target = r['contactCenterNumber'].get('phoneNumber', '')
                        
                    row[f'Context {i+1} Target'] = target

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

    # 2. LOCATION & BUSINESS HOURS
    bh_str = str(row.get('Business Hours Schedule', '')).strip()
    if bh_str and bh_str.lower() != 'nan' and bh_str != 'Configured (Requires Manual Update if Changing)':
        from webapp.cq_hours.utils import parse_intuitive_hours
        try:
            weekly_ranges = parse_intuitive_hours(bh_str)
            if weekly_ranges != "24/7":
                skills["LOCATION_AND_BH"] = {
                    "skillType": "LOCATION_AND_BH",
                    "customLocations": [{
                        "disabled": False,
                        "schedule": {"weeklyRanges": weekly_ranges},
                        "location": {"businessAddress": {}} 
                    }]
                }
        except Exception:
            pass # Ignore malformed hours

    # 3. BOOKING
    booking_link = str(row.get('Booking Link', '')).strip()
    if booking_link and booking_link.lower() != 'nan':
        skills["BOOKING"] = {
            "skillType": "BOOKING",
            "link": booking_link
        }

    # 4. CONTACT DIRECTORY
    sync_dir = str(row.get('Sync Directory (Yes/No)', '')).strip().lower()
    restricted_exts = str(row.get('Directory Restricted Ext IDs', '')).strip()
    if (sync_dir and sync_dir != 'nan') or (restricted_exts and restricted_exts != 'nan'):
        cd_payload = {"skillType": "CONTACT_DIRECTORY"}
        cd_payload["syncDialByNameDirectory"] = (sync_dir in ['yes', 'y', 'true', '1'])
        if restricted_exts and restricted_exts != 'nan':
            ids = [x.strip() for x in restricted_exts.split(',') if x.strip()]
            cd_payload["restrictedExtensions"] = [{"id": x} for x in ids]
        skills["CONTACT_DIRECTORY"] = cd_payload

    # 5. QA (FAQs)
    corpus = []
    for i in range(1, 4):
        q = str(row.get(f'FAQ {i} Question', '')).strip()
        a = str(row.get(f'FAQ {i} Answer', '')).strip()
        if q and q.lower() != 'nan' and a and a.lower() != 'nan':
            corpus.append({"question": q, "answer": a, "origin": "MANUAL"})
    if corpus:
        skills["QA"] = {"skillType": "QA", "corpus": corpus}

    # 6. KNOWLEDGE BASE
    kb_str = str(row.get('Knowledge Base IDs', '')).strip()
    if kb_str and kb_str.lower() != 'nan':
        contexts = [{"contextId": k.strip()} for k in kb_str.split(',') if k.strip()]
        if contexts:
            skills["KNOWLEDGE_BASE"] = {"skillType": "KNOWLEDGE_BASE", "contexts": contexts}

    # 7. CALL ROUTING (Transfer by Context)
    rules = []
    for i in range(1, 11): 
        rule_text = str(row.get(f'Context {i} Rule', '')).strip()
        target = clean_ext_num(row.get(f'Context {i} Target'))
        is_disabled = str(row.get(f'Context {i} Disabled (Yes/No)', '')).strip().lower() in ['yes', 'y', 'true', '1']
        
        if rule_text and rule_text.lower() != 'nan' and target and target.lower() != 'nan':
            rule_obj = {
                "rule": rule_text, 
                "disabled": is_disabled 
            }
            if target.startswith('+') or len(target) >= 10:
                rule_obj["externalNumber"] = target
            else:
                rule_obj["extension"] = {"id": target}
            rules.append(rule_obj)
    
    if rules:
        skills["CALL_ROUTING"] = {"skillType": "CALL_ROUTING", "rules": rules}

    return skills
