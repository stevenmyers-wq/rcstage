# webapp/visualiser/utils.py
import json
import time
from datetime import datetime
from webapp.rc_api import rc_api_call

# Caches to improve performance during recursion
extension_cache = {}
schedule_cache = {}

def get_extension_info(ext_id):
    """Fetches extension info with caching and retry logic."""
    if ext_id in extension_cache: return extension_cache[ext_id]
    
    for i in range(3):
        try:
            info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            # Handle Deleted/Ghost Extensions gracefully
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
        except: 
            time.sleep(0.5)
    return None

def get_business_hours_summary(ext_id):
    """Fetches and formats readable business hours (e.g., 'Mon-Fri: 09:00-17:00')."""
    if ext_id in schedule_cache: return schedule_cache[ext_id]
    
    try:
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        if not resp or 'schedule' not in resp: return "24/7"
            
        schedule = resp['schedule']
        if 'weeklyRanges' not in schedule: return "24/7"

        # Group by Time Range
        ranges = {}
        for day_range in schedule['weeklyRanges']:
            from_t = day_range['from'].split(':', 2)
            to_t = day_range['to'].split(':', 2)
            time_str = f"{from_t[0]}:{from_t[1]}-{to_t[0]}:{to_t[1]}"
            
            if time_str not in ranges: ranges[time_str] = []
            ranges[time_str].append(day_range['dayOfWeek'][:3]) # Mon, Tue
            
        # Format Text
        summary_parts = []
        for time_str, days in ranges.items():
            day_str = ",".join(days)
            if len(days) == 5 and 'Mon' in days and 'Fri' in days: day_str = "Mon-Fri"
            if len(days) == 7: day_str = "Everyday"
            summary_parts.append(f"{day_str}: {time_str}")
            
        result = "<br/>".join(summary_parts)
        schedule_cache[ext_id] = result
        return result
    except:
        return "Schedule Error"

def format_custom_conditions(rule):
    """Extracts Caller ID, DNIS, and Schedule conditions from a rule."""
    conditions = []
    
    if rule.get('callers'):
        callers = [c.get('callerId') or c.get('name') for c in rule['callers']]
        txt = f"Caller ID: {', '.join(callers[:2])}"
        if len(callers) > 2: txt += f" (+{len(callers)-2})"
        conditions.append(txt)
            
    if rule.get('calledNumbers'):
        dnis = [n.get('phoneNumber') for n in rule['calledNumbers']]
        conditions.append(f"Called Num: {', '.join(dnis)}")
        
    if rule.get('schedule', {}).get('weeklyRanges'):
        conditions.append("Specific Schedule")
        
    return "<br/>".join(conditions) if conditions else "No Conditions"

def escape_mermaid(text):
    if not text: return ""
    return json.dumps(text).strip('"').replace('"', "'")

def generate_mermaid_flow(start_ext_id):
    """Generates the advanced Mermaid graph with Logic Nodes."""
    extension_cache.clear()
    schedule_cache.clear()
    
    graph_lines = [
        '---', 'title: Call Flow Diagram', '---', 'graph TD',
        'classDef siteStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20',
        'classDef ivrStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1',
        'classDef queueStyle fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100',
        'classDef userStyle fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121',
        'classDef externalStyle fill:#fff8e1,stroke:#ffc107,stroke-width:2px,color:#ff6f00',
        'classDef logicStyle fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1px,stroke-dasharray: 5 5,color:#4a148c,font-size:12px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    ext_to_node = {}
    node_cnt = 0

    def _trace(ext_id, parent_node=None, link_label="", history=None):
        nonlocal node_cnt
        if history is None: history = []
        
        # 1. Loop Check
        if ext_id in history:
            if ext_id in ext_to_node and parent_node:
                graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)} (Loop)" --> {ext_to_node[ext_id]}')
            return

        if ext_id in ext_to_node:
            if parent_node:
                graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)}" --> {ext_to_node[ext_id]}')
            return

        # 2. Node Creation
        # External Numbers
        if str(ext_id).startswith("ext_"):
            num = str(ext_id).replace("ext_", "")
            nid = f"n{node_cnt}"; node_cnt+=1
            ext_to_node[ext_id] = nid
            graph_lines.append(f'{nid}["[External]<br/><b>{num}</b>"]:::externalStyle')
            if parent_node: graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)}" --> {nid}')
            return

        # Voicemail
        if str(ext_id).startswith("vm_"):
            nid = f"n{node_cnt}"; node_cnt+=1
            ext_to_node[ext_id] = nid
            graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_node: graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)}" --> {nid}')
            return

        # Regular Extension
        info = get_extension_info(ext_id)
        nid = f"n{node_cnt}"; node_cnt+=1
        ext_to_node[ext_id] = nid
        new_hist = history + [ext_id]

        if not info:
            graph_lines.append(f'{nid}["[Unknown ID: {ext_id}]"]:::missingStyle')
            if parent_node: graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)}" --> {nid}')
            return

        etype = info.get('type', 'Unknown')
        if etype == 'Department' and rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"): 
            etype = 'CallQueue'

        label = f"[{etype}]<br/><b>{escape_mermaid(info.get('name'))}</b><br/>{info.get('extensionNumber','')}"
        style = {'Site':'siteStyle', 'IvrMenu':'ivrStyle', 'CallQueue':'queueStyle'}.get(etype, 'userStyle')
        graph_lines.append(f'{nid}["{label}"]:::{style}')
        
        if parent_node:
            graph_lines.append(f'{parent_node} -- "{escape_mermaid(link_label)}" --> {nid}')

        # 3. Details (Queue Members)
        if etype == 'CallQueue':
            try:
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    m_txt = [f"- {escape_mermaid(get_extension_info(m['id']).get('name','?'))}" 
                             for m in m_resp['records'][:6] if get_extension_info(m['id'])]
                    if len(m_resp['records']) > 6: m_txt.append(f"... +{len(m_resp['records'])-6}")
                    if m_txt:
                        iid = f"info_{node_cnt}"; node_cnt+=1
                        graph_lines.append(f'{iid}["<b>Agents:</b><br/>{ "<br/>".join(m_txt) }"]:::logicStyle')
                        graph_lines.append(f'{nid} -.-> {iid}')
            except: pass

        # 4. Rules Logic
        if etype in ['User', 'CallQueue', 'Site', 'Department']:
            try:
                rules = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        if not r.get('enabled'): continue
                        
                        rtype = r.get('type')
                        rname = r.get('name', 'Rule')
                        action = r.get('callHandlingAction')
                        
                        # Logic Node Text
                        logic_text = ""
                        if rtype == 'BusinessHours':
                            rname = "Business Hours"
                            logic_text = f"<b>{rname}</b><br/>{get_business_hours_summary(ext_id)}"
                        elif rtype == 'Custom':
                            logic_text = f"<b>{rname}</b><br/>IF: {format_custom_conditions(r)}"
                        else:
                            rname = "After Hours" if rtype == 'AfterHours' else rname
                            logic_text = f"<b>{rname}</b>"

                        # Find Target
                        target_id = None
                        if action == 'TransferToExtension':
                            target_id = r.get('transfer', {}).get('extension', {}).get('id')
                        elif action == 'UnconditionalForwarding':
                            ph = r.get('unconditionalForwarding', {}).get('phoneNumber')
                            ex = r.get('unconditionalForwarding', {}).get('extension', {})
                            if ex.get('id'): target_id = ex['id']
                            elif ph: target_id = f"ext_{ph}"
                        elif action == 'TakeMessagesOnly':
                            target_id = f"vm_{ext_id}"

                        if target_id:
                            # Use Logic Node for complex rules
                            if rtype in ['BusinessHours', 'Custom']:
                                lid = f"logic_{node_cnt}"; node_cnt+=1
                                graph_lines.append(f'{lid}{{"{logic_text}"}}:::logicStyle')
                                graph_lines.append(f'{nid} --> {lid}')
                                _trace(target_id, lid, "Matches", new_hist)
                            else:
                                _trace(target_id, nid, rname, new_hist)
                        else:
                            # Non-routing rules
                            detail = action
                            if action == 'PlayAnnouncementOnly': detail = "Play Announcement"
                            iid = f"cfg_{node_cnt}"; node_cnt+=1
                            graph_lines.append(f'{iid}["<b>{rname}</b><br/>{detail}"]:::logicStyle')
                            graph_lines.append(f'{nid} -.-> {iid}')

            except Exception as e: print(f"Rule Error: {e}")

        # 5. IVR Logic
        if etype == 'IvrMenu':
            try:
                ivr = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr and ivr.get('actions'):
                    for act in ivr['actions']:
                        key = act.get('input', '?')
                        if act.get('extension', {}).get('id'):
                            _trace(act['extension']['id'], nid, f"Key {key}", new_hist)
                        elif act.get('phoneNumber'):
                            _trace(f"ext_{act['phoneNumber']}", nid, f"Key {key}", new_hist)
            except: pass

    _trace(start_ext_id)
    return "\n".join(graph_lines)
