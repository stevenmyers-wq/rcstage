# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

# Caches
extension_cache = {}
schedule_cache = {}

def get_extension_info(ext_id):
    if ext_id in extension_cache: return extension_cache[ext_id]
    for i in range(3):
        try:
            info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
        except: time.sleep(0.5)
    return None

def get_schedule_summary(ext_id):
    """
    Fetches Business Hours for Users, Queues, and Sites.
    """
    if ext_id in schedule_cache: return schedule_cache[ext_id]
    
    try:
        # This endpoint works for Extensions, Queues, and Sites
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        
        if not resp or 'schedule' not in resp:
            return "24/7 (All Day)"
            
        schedule = resp['schedule']
        if 'weeklyRanges' not in schedule:
            return "24/7 (All Day)"

        # Group similar time ranges
        # Format: { "09:00-17:00": ["Mon", "Tue"] }
        ranges = {}
        for day_range in schedule['weeklyRanges']:
            # Parse times safely
            f_t = day_range.get('from', '00:00').split(':')
            t_t = day_range.get('to', '23:59').split(':')
            time_str = f"{f_t[0]}:{f_t[1]}-{t_t[0]}:{t_t[1]}"
            
            if time_str not in ranges: ranges[time_str] = []
            day = day_range.get('dayOfWeek', '???')[:3]
            ranges[time_str].append(day)
            
        # Build text string
        lines = []
        for t_str, days in ranges.items():
            day_label = ",".join(days)
            if len(days) == 5 and 'Mon' in days and 'Fri' in days: day_label = "Mon-Fri"
            if len(days) == 7: day_label = "Everyday"
            lines.append(f"{day_label}: {t_str}")
            
        res = "<br/>".join(lines)
        schedule_cache[ext_id] = res
        return res
    except Exception as e:
        return "Schedule Unavailable"

def format_custom_rule(rule):
    """
    Extracts readable conditions from a Custom Rule.
    """
    conds = []
    
    # 1. Who is calling? (Caller ID)
    if rule.get('callers'):
        names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
        if len(names) > 2:
            conds.append(f"From: {', '.join(names[:2])} (+{len(names)-2})")
        else:
            conds.append(f"From: {', '.join(names)}")

    # 2. What number did they dial? (DNIS)
    if rule.get('calledNumbers'):
        nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
        conds.append(f"Dialed: {', '.join(nums)}")
        
    # 3. When? (Specific Schedule)
    if rule.get('schedule', {}).get('weeklyRanges'):
        conds.append("During Specific Times")
        
    if not conds:
        return "Matches All Calls"
        
    return "<br/>".join(conds)

def clean_text(text):
    """
    Strict sanitizer for Mermaid labels. 
    Removes quotes and special characters that break the syntax.
    """
    if not text: return ""
    # Replace double quotes with single, remove others
    return str(text).replace('"', "'").replace('#', '').strip()

def generate_mermaid_flow(start_ext_id):
    extension_cache.clear()
    schedule_cache.clear()
    
    # Define styles - PROFESSIONAL, NO ICONS
    graph_lines = [
        '---', 'title: Call Flow Diagram', '---', 'graph TD',
        'classDef siteStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20',
        'classDef ivrStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1',
        'classDef queueStyle fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100',
        'classDef userStyle fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121',
        'classDef logicStyle fill:#fff,stroke:#7b1fa2,stroke-width:1px,stroke-dasharray: 5 5,color:#4a148c,font-size:12px',
        'classDef infoStyle fill:#fff,stroke:#b0bec5,stroke-width:1px,stroke-dasharray: 2 2,color:#37474f,font-size:11px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    node_map = {}
    node_counter = 0

    def _trace(ext_id, parent_id=None, link_label="", history=None):
        nonlocal node_counter
        if history is None: history = []
        
        # --- Cycle Prevention ---
        if ext_id in history:
            if ext_id in node_map and parent_id:
                graph_lines.append(f'{parent_id} -- "{clean_text(link_label)} (Loop)" --> {node_map[ext_id]}')
            return

        if ext_id in node_map:
            if parent_id:
                graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {node_map[ext_id]}')
            return

        # --- Node Creation ---
        # 1. External Number (PSTN)
        if str(ext_id).startswith("ext_"):
            num = str(ext_id).replace("ext_", "")
            nid = f"n{node_counter}"; node_counter += 1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}["[External Number]<br/><b>{num}</b>"]:::siteStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        # 2. Voicemail Box
        if str(ext_id).startswith("vm_"):
            nid = f"n{node_counter}"; node_counter += 1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}(("[Voicemail Box]")):::userStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        # 3. Standard Extension
        info = get_extension_info(ext_id)
        nid = f"n{node_counter}"; node_counter += 1
        node_map[ext_id] = nid
        new_history = history + [ext_id]

        if not info:
            graph_lines.append(f'{nid}["[Unknown ID: {ext_id}]"]:::missingStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        # Normalize Department -> CallQueue if applicable
        if e_type == 'Department' and rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        # Professional Label (No Icons)
        label = f"[{e_type}]<br/><b>{clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        
        graph_lines.append(f'{nid}["{label}"]:::{style}')
        if parent_id:
             graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')

        # --- Details: Queue Members ---
        if e_type == 'CallQueue':
            try:
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    # Fetch first 6 members
                    m_list = []
                    for m in m_resp['records'][:6]:
                        mi = get_extension_info(m['id'])
                        if mi: m_list.append(f"- {clean_text(mi.get('name'))}")
                    
                    if len(m_resp['records']) > 6: m_list.append(f"... {len(m_resp['records'])-6} more")
                    
                    if m_list:
                        iid = f"info_{node_counter}"; node_counter += 1
                        graph_lines.append(f'{iid}["<b>Agents:</b><br/>{ "<br/>".join(m_list) }"]:::infoStyle')
                        graph_lines.append(f'{nid} -.-> {iid}')
            except: pass

        # --- Logic: Answering Rules (Business Hours, etc) ---
        if e_type in ['User', 'CallQueue', 'Site', 'Department', 'ApplicationExtension']:
            try:
                rules = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        if not r.get('enabled'): continue
                        
                        r_type = r.get('type', 'Custom')
                        r_name = r.get('name', 'Rule')
                        action = r.get('callHandlingAction')
                        
                        # Build the "Logic Node" content
                        logic_desc = ""
                        
                        if r_type == 'BusinessHours':
                            r_name = "Business Hours"
                            sched = get_schedule_summary(ext_id)
                            logic_desc = f"<b>{r_name}</b><br/>{sched}"
                        elif r_type == 'AfterHours':
                            r_name = "After Hours"
                            logic_desc = f"<b>{r_name}</b>"
                        elif r_type == 'Custom':
                            conds = format_custom_rule(r)
                            logic_desc = f"<b>Custom: {clean_text(r_name)}</b><br/>{conds}"
                        else:
                            logic_desc = f"<b>{clean_text(r_name)}</b>"

                        # Determine Target
                        target = None
                        if action == 'TransferToExtension':
                            target = r.get('transfer', {}).get('extension', {}).get('id')
                        elif action == 'UnconditionalForwarding':
                            ph = r.get('unconditionalForwarding', {}).get('phoneNumber')
                            ex = r.get('unconditionalForwarding', {}).get('extension', {})
                            if ex.get('id'): target = ex['id']
                            elif ph: target = f"ext_{ph}"
                        elif action == 'TakeMessagesOnly':
                            target = f"vm_{ext_id}"

                        if target:
                            # If it's a complex rule (Schedule/Custom), draw a Logic Node (Hexagon)
                            # We use clean_text to ensure no quotes break the {{ }} syntax
                            if r_type in ['BusinessHours', 'Custom']:
                                lid = f"log_{node_counter}"; node_counter += 1
                                # IMPORTANT: Sanitized logic_desc here
                                graph_lines.append(f'{lid}{{"{clean_text(logic_desc)}"}}:::logicStyle')
                                graph_lines.append(f'{nid} --> {lid}')
                                _trace(target, lid, "Matches", new_history)
                            else:
                                # Direct link for simple stuff (After Hours)
                                _trace(target, nid, r_name, new_history)
                        else:
                            # Non-transfer rule (e.g. Announcement)
                            detail = action
                            if action == 'PlayAnnouncementOnly': detail = "Play Announcement"
                            
                            iid = f"cfg_{node_counter}"; node_counter += 1
                            graph_lines.append(f'{iid}["{clean_text(logic_desc)}<br/>Action: {detail}"]:::logicStyle')
                            graph_lines.append(f'{nid} -.-> {iid}')

            except Exception as e:
                print(f"Rule Error {ext_id}: {e}")

        # --- Logic: IVR ---
        if e_type == 'IvrMenu':
            try:
                ivr = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr and ivr.get('actions'):
                    for act in ivr['actions']:
                        key = act.get('input', '?')
                        if act.get('extension', {}).get('id'):
                            _trace(act['extension']['id'], nid, f"Key {key}", new_history)
                        elif act.get('phoneNumber'):
                            _trace(f"ext_{act['phoneNumber']}", nid, f"Key {key}", new_history)
            except: pass

    _trace(start_ext_id)
    return "\n".join(graph_lines)
