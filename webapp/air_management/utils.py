import pandas as pd
import re
import time
import io
from datetime import datetime
from webapp.rc_api import rc_api_call

MAX_FAQS = 25
MAX_ROUTING = 25

export_progress_store = {}

def get_ext_directory(token=None):
    """Fetches the account directory to map Extension Numbers to internal IDs."""
    exts = []
    page = 1
    while True:
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension?perPage=1000&page={page}', token=token, raise_error=False)
        if not resp or 'records' not in resp: break
        exts.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): break
        page += 1
        time.sleep(0.05)
        
    dir_map = {}
    for e in exts:
        eid = str(e.get('id', ''))
        enum = str(e.get('extensionNumber', ''))
        name = str(e.get('name', ''))
        if eid: dir_map[eid] = {'id': eid, 'ext': enum, 'name': name}
        if enum: dir_map[enum] = {'id': eid, 'ext': enum, 'name': name}
    return dir_map

def format_ext_display(ext_id, dir_map):
    """Converts an internal ID to a human-readable Name (Ext Number) format."""
    if not ext_id: return ""
    ext_id = str(ext_id)
    if dir_map and ext_id in dir_map:
        info = dir_map[ext_id]
        if info['ext']: return f"{info['name']} (Ext {info['ext']})"
        return info['name']
    return ext_id

def extract_id(val, dir_map):
    """Safely extracts the internal Extension ID from a string, number, or formatted name."""
    if not val: return ''
    val = str(val).strip()
    
    m = re.search(r'\(Ext (\d+)\)', val, re.IGNORECASE)
    if m:
        ext_num = m.group(1)
        if dir_map and ext_num in dir_map: return dir_map[ext_num]['id']
        
    if dir_map and val in dir_map:
        return dir_map[val]['id']
        
    return val

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

def parse_assistant_to_row(assistant, dir_map, token=None):
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
        'Fallback Extension': format_ext_display(fallback.get('id'), dir_map),
        'Site ID': assistant.get('siteId', ''),
        'Website': assistant.get('website', ''),
        'Prompt Template': assistant.get('promptTemplate', ''),
        'Idle Action (BH)': bh_action.get('actionType', ''),
        'Idle Target (BH)': format_ext_display(bh_action.get('extension', {}).get('id'), dir_map),
        'Idle Action (AH)': ah_action.get('actionType', ''),
        'Idle Target (AH)': format_ext_display(ah_action.get('extension', {}).get('id'), dir_map),
        'Greeting (BH Text)': '',
        'Greeting (AH Text)': '',
        'Business Hours Schedule': '',
        'Booking Link': '',
        'Sync Directory (Yes/No)': '',
        'Directory Restricted Ext IDs': '',
        'Knowledge Base IDs': ''
    }

    for i in range(1, MAX_FAQS + 1):
        row[f'FAQ {i} Question'] = ''
        row[f'FAQ {i} Answer'] = ''

    for i in range(1, MAX_ROUTING + 1):
        row[f'Context {i} Rule'] = ''
        row[f'Context {i} Action'] = ''
        row[f'Context {i} Target'] = ''
        row[f'Context {i} Disabled (Yes/No)'] = ''

    if 'skills' in assistant and token:
        for s_stub in assistant['skills']:
            skill_detail = fetch_skill_details(s_stub['id'], token)
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
                for i, qa in enumerate(corpus[:MAX_FAQS]): 
                    row[f'FAQ {i+1} Question'] = qa.get('question', '')
                    row[f'FAQ {i+1} Answer'] = qa.get('answer', '')

            elif stype == 'KNOWLEDGE_BASE':
                contexts = skill.get('contexts', [])
                row['Knowledge Base IDs'] = ', '.join([c.get('contextId', '') for c in contexts])
                
            elif stype == 'CALL_ROUTING':
                rules = skill.get('rules', [])
                for i, r in enumerate(rules[:MAX_ROUTING]): 
                    row[f'Context {i+1} Rule'] = r.get('rule', '')
                    row[f'Context {i+1} Disabled (Yes/No)'] = 'Yes' if r.get('disabled') else 'No'
                    
                    action = ''
                    target = ''
                    if r.get('externalNumber'):
                        action = 'External'
                        target = r['externalNumber']
                    elif r.get('contactCenterNumber'):
                        action = 'Contact Centre'
                        target = r['contactCenterNumber'].get('phoneNumber', r['contactCenterNumber'].get('id', ''))
                    elif r.get('extension'):
                        action = 'Extension'
                        target = format_ext_display(r['extension'].get('id', ''), dir_map)
                        
                    row[f'Context {i+1} Action'] = action
                    row[f'Context {i+1} Target'] = target

    return row

def safe_str(val, default=''):
    if pd.isna(val): return default
    s = str(val).strip()
    if s.lower() == 'nan': return default
    return s

def clean_ext_num(val):
    if pd.isna(val): return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    if s.endswith('.0'): s = s[:-2]
    return s

def build_assistant_payload(row, dir_map):
    """Build the core API payload for the base Assistant."""
    sys_type = safe_str(row.get('System Type'), 'PBX_VOICE')
    if not sys_type: sys_type = 'PBX_VOICE'

    voice_name = safe_str(row.get('Voice Name'), 'Kore')
    if not voice_name: voice_name = 'Kore'

    payload = {
        "name": safe_str(row.get('Name')),
        "extensionNumber": clean_ext_num(row.get('Extension Number')),
        "companyDescription": safe_str(row.get('Company Description')),
        "systemSettings": {
            "systemType": sys_type,
            "voiceName": voice_name
        }
    }
    
    langs = safe_str(row.get('Languages'))
    if langs:
        payload["languages"] = [l.strip() for l in langs.split(',') if l.strip()]

    def add_str(field, key):
        val = safe_str(row.get(field))
        if val: payload[key] = val

    add_str('Site ID', 'siteId')
    add_str('Prompt Template', 'promptTemplate')
    add_str('Website', 'website')

    fallback_id = extract_id(row.get('Fallback Extension'), dir_map)
    if fallback_id:
        payload["fallbackExtension"] = {"id": fallback_id}
        
    bh_act = safe_str(row.get('Idle Action (BH)'))
    bh_tgt = extract_id(row.get('Idle Target (BH)'), dir_map)
    ah_act = safe_str(row.get('Idle Action (AH)'))
    ah_tgt = extract_id(row.get('Idle Target (AH)'), dir_map)

    if bh_act or ah_act:
        idle_rule = {}
        
        bh_final = bh_act if bh_act else 'Disconnect'
        idle_rule['businessHours'] = {'actionType': bh_final}
        if bh_final == 'Extension':
            if not bh_tgt: raise ValueError("Idle Target (BH) is required when Idle Action is 'Extension'")
            idle_rule['businessHours']['extension'] = {'id': bh_tgt}
            
        ah_final = ah_act if ah_act else 'Disconnect'
        idle_rule['closedHours'] = {'actionType': ah_final}
        if ah_final == 'Extension':
            if not ah_tgt: raise ValueError("Idle Target (AH) is required when Idle Action is 'Extension'")
            idle_rule['closedHours']['extension'] = {'id': ah_tgt}
            
        payload['idleCallerRule'] = idle_rule
        
    return payload

def build_skills_payloads(row, dir_map):
    """Parses the Excel row and returns a dictionary of SkillOption payloads to apply."""
    skills = {}

    # 1. GREETING
    bh_greet = safe_str(row.get('Greeting (BH Text)'))
    ah_greet = safe_str(row.get('Greeting (AH Text)'))
    if bh_greet or ah_greet:
        greet_payload = {"skillType": "GREETING"}
        if bh_greet: greet_payload["businessHours"] = {"text": bh_greet}
        if ah_greet: greet_payload["closedHours"] = {"text": ah_greet}
        skills["GREETING"] = greet_payload

    # 2. LOCATION & BUSINESS HOURS
    bh_str = safe_str(row.get('Business Hours Schedule'))
    if bh_str and bh_str != 'Configured (Requires Manual Update if Changing)':
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
            pass 

    # 3. BOOKING
    booking_link = safe_str(row.get('Booking Link'))
    if booking_link:
        skills["BOOKING"] = {
            "skillType": "BOOKING",
            "link": booking_link
        }

    # 4. CONTACT DIRECTORY
    sync_dir = safe_str(row.get('Sync Directory (Yes/No)')).lower()
    restricted_exts = safe_str(row.get('Directory Restricted Ext IDs'))
    if sync_dir or restricted_exts:
        cd_payload = {"skillType": "CONTACT_DIRECTORY"}
        cd_payload["syncDialByNameDirectory"] = (sync_dir in ['yes', 'y', 'true', '1'])
        if restricted_exts:
            ids = [extract_id(x.strip(), dir_map) for x in restricted_exts.split(',') if x.strip()]
            cd_payload["restrictedExtensions"] = [{"id": x} for x in ids]
        skills["CONTACT_DIRECTORY"] = cd_payload

    # 5. QA (FAQs)
    corpus = []
    faq_indices = [int(re.search(r'\d+', str(k)).group()) for k in row.keys() if re.match(r'^FAQ \d+ Question$', str(k).strip())]
    max_faq = max(faq_indices) if faq_indices else 0
    
    for i in range(1, max_faq + 1):
        q = safe_str(row.get(f'FAQ {i} Question'))
        a = safe_str(row.get(f'FAQ {i} Answer'))
        if q and a:
            corpus.append({"question": q, "answer": a, "origin": "MANUAL"})
            
    if corpus:
        skills["QA"] = {"skillType": "QA", "corpus": corpus}

    # 6. KNOWLEDGE BASE
    kb_str = safe_str(row.get('Knowledge Base IDs'))
    if kb_str:
        contexts = [{"contextId": k.strip()} for k in kb_str.split(',') if k.strip()]
        if contexts:
            skills["KNOWLEDGE_BASE"] = {"skillType": "KNOWLEDGE_BASE", "contexts": contexts}

    # 7. CALL ROUTING (Transfer by Context)
    rules = []
    ctx_indices = [int(re.search(r'\d+', str(k)).group()) for k in row.keys() if re.match(r'^Context \d+ Rule$', str(k).strip())]
    max_ctx = max(ctx_indices) if ctx_indices else 0
    
    for i in range(1, max_ctx + 1): 
        rule_text = safe_str(row.get(f'Context {i} Rule'))
        action = safe_str(row.get(f'Context {i} Action')).lower()
        target = safe_str(row.get(f'Context {i} Target'))
        is_disabled = safe_str(row.get(f'Context {i} Disabled (Yes/No)')).lower() in ['yes', 'y', 'true', '1']
        
        if rule_text and target and action:
            rule_obj = {
                "rule": rule_text, 
                "disabled": is_disabled 
            }
            
            if 'external' in action:
                rule_obj["externalNumber"] = target
            elif 'contact centre' in action or 'contact center' in action:
                if target.isdigit():
                    try:
                        rule_obj["contactCenterNumber"] = {"id": int(target), "phoneNumber": target}
                    except:
                        rule_obj["contactCenterNumber"] = {"phoneNumber": target}
                else:
                    rule_obj["contactCenterNumber"] = {"phoneNumber": target}
            else: 
                rule_obj["extension"] = {"id": extract_id(target, dir_map)}
                
            rules.append(rule_obj)
    
    if rules:
        skills["CALL_ROUTING"] = {"skillType": "CALL_ROUTING", "rules": rules}

    return skills

def get_air_graph(air_id, dir_map, token=None):
    """Builds a node and edge graph for a specific AIR to render in Cytoscape."""
    assistant = rc_api_call(f'/ai/iva/v1/accounts/~/assistants/{air_id}', token=token, raise_error=True)
    
    nodes = []
    edges = []
    node_ids = set()
    
    def add_node(nid, label, ntype, sublabel="", tooltip=""):
        if nid not in node_ids:
            nodes.append({
                "data": {"id": str(nid), "label": str(label), "type": ntype, "sublabel": str(sublabel), "tooltip": str(tooltip)}
            })
            node_ids.add(str(nid))
            
    def add_edge(src, tgt, label):
        safe_label = str(label)
        if len(safe_label) > 100:
            safe_label = safe_label[:97] + "..."
        edges.append({"data": {"source": str(src), "target": str(tgt), "label": safe_label}})
        
    name = assistant.get('name', 'Unknown AIR')
    ext_num = assistant.get('extensionNumber', '')
    
    tooltip = []
    tooltip.append(f"System Type: {assistant.get('systemSettings', {}).get('systemType', '')}")
    tooltip.append(f"Voice: {assistant.get('systemSettings', {}).get('voiceName', '')}")
    
    skills_resp = rc_api_call(f'/ai/iva/v1/accounts/~/assistants/{air_id}/skills', token=token, raise_error=False)
    skills = skills_resp.get('records', []) if skills_resp else []
    
    for s in skills:
        skill_detail = fetch_skill_details(s['id'], token)
        if not skill_detail or 'skill' not in skill_detail: continue
        sk = skill_detail['skill']
        stype = sk.get('skillType')
        
        if stype == 'GREETING':
            tooltip.append(f"Greeting (BH): {sk.get('businessHours', {}).get('text', 'None')}")
        elif stype == 'QA':
            tooltip.append(f"FAQs Configured: {len(sk.get('corpus', []))}")
        elif stype == 'KNOWLEDGE_BASE':
            ctxs = sk.get('contexts', [])
            tooltip.append(f"Knowledge Bases Attached: {len(ctxs)}")
            
    add_node(air_id, name, "autoreceptionist", sublabel=f"AIR Ext {ext_num}", tooltip="\n".join(tooltip))
    
    # Map Fallback
    fallback = assistant.get('fallbackExtension', {}).get('id')
    if fallback:
        add_node(fallback, format_ext_display(fallback, dir_map), "user", sublabel="Fallback Destination")
        add_edge(air_id, fallback, "Fallback")
        
    # Map Idle Rules
    idle = assistant.get('idleCallerRule', {})
    bh = idle.get('businessHours', {})
    ah = idle.get('closedHours', {})
    
    if bh.get('actionType') == 'Extension' and bh.get('extension', {}).get('id'):
        tgt = bh['extension']['id']
        add_node(tgt, format_ext_display(tgt, dir_map), "user", sublabel="Idle Timeout (BH)")
        add_edge(air_id, tgt, "Idle (BH)")
    elif bh.get('actionType') == 'Disconnect':
        add_node(f"disc_bh_{air_id}", "Disconnect", "external")
        add_edge(air_id, f"disc_bh_{air_id}", "Idle (BH)")

    if ah.get('actionType') == 'Extension' and ah.get('extension', {}).get('id'):
        tgt = ah['extension']['id']
        add_node(tgt, format_ext_display(tgt, dir_map), "user", sublabel="Idle Timeout (AH)")
        add_edge(air_id, tgt, "Idle (AH)")
    elif ah.get('actionType') == 'Disconnect':
        add_node(f"disc_ah_{air_id}", "Disconnect", "external")
        add_edge(air_id, f"disc_ah_{air_id}", "Idle (AH)")
        
    # Map Context Routing Rules
    for s in skills:
        skill_detail = fetch_skill_details(s['id'], token)
        if not skill_detail or 'skill' not in skill_detail: continue
        sk = skill_detail['skill']
        
        if sk.get('skillType') == 'CALL_ROUTING':
            rules = sk.get('rules', [])
            for i, r in enumerate(rules):
                if r.get('disabled'): continue
                
                tgt_id = None
                tgt_name = None
                ntype = "user"
                
                if r.get('extension'):
                    tgt_id = str(r['extension'].get('id', ''))
                    tgt_name = format_ext_display(tgt_id, dir_map)
                elif r.get('externalNumber'):
                    tgt_id = f"ext_{r['externalNumber']}_{i}" 
                    tgt_name = r['externalNumber']
                    ntype = "external"
                elif r.get('contactCenterNumber'):
                    c_num = r['contactCenterNumber']
                    raw_tgt = str(c_num.get('id') or c_num.get('phoneNumber', ''))
                    tgt_id = f"cx_{raw_tgt}_{i}"
                    tgt_name = f"CXone: {c_num.get('phoneNumber', raw_tgt)}"
                    ntype = "queue"
                    
                if tgt_id:
                    add_node(tgt_id, tgt_name, ntype, sublabel="Context Target")
                    add_edge(air_id, tgt_id, r.get('rule', f'Context {i+1}'))
                    
    return {"nodes": nodes, "edges": edges}

def run_transcript_export(task_id, date_from, date_to, air_id, token):
    """Background worker to fetch conversations and compile transcripts."""
    try:
        export_progress_store[task_id] = {'current': 0, 'total': 1, 'status': 'running', 'file_data': None, 'message': 'Fetching conversations...'}
        
        conversations = []
        page = 1
        while True:
            payload = {
                "startDateFrom": date_from,
                "startDateTo": date_to,
                "perPage": 100,
                "page": page
            }
            if air_id:
                payload["assistantIds"] = [air_id]
                
            resp = rc_api_call('/ai/iva/v1/accounts/~/conversations/search', method='POST', json=payload, token=token, raise_error=False)
            if not resp or 'records' not in resp:
                break
            conversations.extend(resp['records'])
            if not resp.get('paging', {}).get('nextPage'):
                break
            page += 1
            time.sleep(0.1)

        total_convs = len(conversations)
        export_progress_store[task_id]['total'] = total_convs if total_convs > 0 else 1
        
        if total_convs == 0:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                pd.DataFrame([{"Message": "No conversations found for the selected criteria."}]).to_excel(writer, index=False, sheet_name='Call Log')
            export_progress_store[task_id]['file_data'] = output.getvalue()
            export_progress_store[task_id]['status'] = 'completed'
            return

        call_log_data = []
        transcript_data = []

        for i, conv in enumerate(conversations):
            export_progress_store[task_id]['current'] = i
            export_progress_store[task_id]['message'] = f'Fetching transcript {i+1} of {total_convs}...'
            
            conv_id = conv.get('id')
            call_log_data.append({
                'Conversation ID': conv_id,
                'Assistant Name': conv.get('assistantName', ''),
                'Caller Number': conv.get('callerFormattedNumber', conv.get('callerNumber', '')),
                'Start Time': conv.get('creationTime', ''),
                'End Time': conv.get('callEndTime', ''),
                'Duration (s)': conv.get('callDurationSeconds', 0),
                'End Reason': conv.get('callEndReason', ''),
                'Type': conv.get('type', ''),
                'Contains SMS': conv.get('containsSms', False)
            })

            msg_page = 1
            while True:
                msg_resp = rc_api_call(f'/ai/iva/v1/accounts/~/conversations/{conv_id}/messages?perPage=100&page={msg_page}', token=token, raise_error=False)
                if not msg_resp or 'records' not in msg_resp:
                    break
                
                for msg in msg_resp['records']:
                    transcript_data.append({
                        'Conversation ID': conv_id,
                        'Timestamp': msg.get('creationTime', ''),
                        'Role': msg.get('producerRole', ''),
                        'Text': msg.get('text', ''),
                        'Tool Triggered': msg.get('toolName', ''),
                        'Tool Output': msg.get('toolOutput', '')
                    })
                    
                if not msg_resp.get('paging', {}).get('nextPage'):
                    break
                msg_page += 1
                time.sleep(0.05)

        export_progress_store[task_id]['message'] = 'Compiling Excel file...'
        
        df_log = pd.DataFrame(call_log_data)
        df_transcripts = pd.DataFrame(transcript_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_log.to_excel(writer, index=False, sheet_name='Call Log')
            if not df_transcripts.empty:
                df_transcripts.to_excel(writer, index=False, sheet_name='Transcripts')
            else:
                pd.DataFrame([{"Message": "No transcripts found."}]).to_excel(writer, index=False, sheet_name='Transcripts')
        
        export_progress_store[task_id]['file_data'] = output.getvalue()
        export_progress_store[task_id]['status'] = 'completed'

    except Exception as e:
        export_progress_store[task_id]['status'] = 'error'
        export_progress_store[task_id]['error'] = str(e)
