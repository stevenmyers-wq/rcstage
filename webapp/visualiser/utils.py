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

def parse_weekly_ranges(weekly_ranges):
    """
    Robust parser for weekly schedules. 
    Handles null values, missing keys, and odd formats safely.
    """
    if not weekly_ranges: return None
    
    try:
        time_groups = {}
        for item in weekly_ranges:
            # 1. Handle Nulls safely (RC sends null for 00:00 or 23:59 sometimes)
            raw_from = item.get('from')
            raw_to = item.get('to')
            
            s_str = str(raw_from) if raw_from else '00:00'
            e_str = str(raw_to) if raw_to else '23:59'
            
            # 2. Extract HH:MM
            # Split ensures we discard seconds if present (09:00:00 -> 09:00)
            s_parts = s_str.split(':')
            e_parts = e_str.split(':')
            
            if len(s_parts) >= 2: s_str = f"{s_parts[0]}:{s_parts[1]}"
            if len(e_parts) >= 2: e_str = f"{e_parts[0]}:{e_parts[1]}"
            
            t_str = f"{s_str}-{e_str}"
            
            # 3. Get Day
            day = item.get('dayOfWeek', '???')[:3]
            
            if t_str not in time_groups: time_groups[t_str] = []
            time_groups[t_str].append(day)

        # 4. Sort and Format
        lines = []
        # Logical sort order for days
        day_map = {'Sun':0, 'Mon':1, 'Tue':2, 'Wed':3, 'Thu':4, 'Fri':5, 'Sat':6}
        
        for t_str, days in time_groups.items():
            # Sort days: Mon, Tue, Wed...
            days.sort(key=lambda d: day_map.get(d, 99))
            
            day_lbl = ",".join(days)
            if len(days) == 5 and 'Mon' in days and 'Fri' in days: day_lbl = "Mon-Fri"
            if len(days) == 7: day_lbl = "Everyday"
            if len(days) == 2 and 'Sat' in days and 'Sun' in days: day_lbl = "Weekends"
            
            lines.append(f"{day_lbl}: {t_str}")
            
        return "<br/>".join(lines)
        
    except Exception as e:
        print(f"Schedule Parse Error: {e}")
        # FALLBACK: If pretty printing fails, dump the raw data so user sees SOMETHING.
        try:
            fallback = []
            for item in weekly_ranges:
                d = item.get('dayOfWeek', '?')[:3]
                s = item.get('from', '00:00')
                e = item.get('to', '23:59')
                fallback.append(f"{d} {s}-{e}")
            return "<br/>".join(fallback)
        except:
            return "Invalid Data"

def get_schedule_summary(ext_id):
    """Fetches Business Hours."""
    if ext_id in schedule_cache: return schedule_cache[ext_id]
    try:
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        if not resp or 'schedule' not in resp: return "24/7 (All Day)"
        
        sched = resp['schedule']
        if not sched.get('weeklyRanges'): return "24/7 (Open)"
        
        txt = parse_weekly_ranges(sched['weeklyRanges'])
        schedule_cache[ext_id] = txt if txt else "24/7"
        return schedule_cache[ext_id]
    except:
        return "Schedule Error"

def format_custom_rule(rule):
    """Parses Custom Rules with safe fallback."""
    try:
        conds = []
        
        # 1. Caller ID
        if rule.get('callers'):
            names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
            txt = f"From: {', '.join(names[:2])}"
            if len(names) > 2: txt += f" (+{len(names)-2})"
            conds.append(txt)

        # 2. Called Number
        if rule.get('calledNumbers'):
            nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
            conds.append(f"To: {', '.join(nums)}")
            
        # 3. Schedules
        schedule = rule.get('schedule', {})
        if schedule.get('weeklyRanges'):
            w_text = parse_weekly_ranges(schedule['weeklyRanges'])
            if w_text: conds.append(f"<b>Time:</b> {w_text}")
            
        if schedule.get('ranges'):
            # Ranges are usually holidays. Show the first one or a count.
            r_count = len(schedule['ranges'])
            first_r = schedule['ranges'][0]
            f_from = first_r.get('from', '').split('T')[0] # 2023-12-25
            f_to = first_r.get('to', '').split('T')[0]
            conds.append(f"<b>Date:</b> {f_from} to {f_to} (+{r_count-1} more)" if r_count > 1 else f"<b>Date:</b> {f_from} to {f_to}")

        if not conds: return "Matches All"
        return "<br/>".join(conds)
        
    except Exception as e:
        print(f"Error parsing custom rule: {e}")
        return "Custom Rule (Complex)"

def clean_text(text):
    if not text: return ""
    return str(text).replace('"', "'").replace('#', '').strip()

def generate_mermaid_flow(start_ext_id):
    extension_cache.clear()
    schedule_cache.clear()
    
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
        
        # Cycle Check
        if ext_id in history:
            if ext_id in node_map and parent_id:
                graph_lines.append(f'{parent_id} -- "{clean_text(link_label)} (Loop)" --> {node_map[ext_id]}')
            return
        if ext_id in node_map:
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {node_map[ext_id]}')
            return

        # Node Creation
        # External
        if str(ext_id).startswith("ext_"):
            num = str(ext_id).replace("ext_", "")
            nid = f"n{node_counter}"; node_counter += 1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}["[External]<br/><b>{num}</b>"]:::siteStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return
        # Voicemail
        if str(ext_id).startswith("vm_"):
            nid = f"n{node_counter}"; node_counter += 1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        # Regular Ext
        info = get_extension_info(ext_id)
        nid = f"n{node_counter}"; node_counter += 1
        node_map[ext_id] = nid
        new_history = history + [ext_id]

        if not info:
            graph_lines.append(f'{nid}["[Unknown: {ext_id}]"]:::missingStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        if e_type == 'Department' and rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        graph_lines.append(f'{nid}["{label}"]:::{style}')
        if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')

        # Queue Agents
        if e_type == 'CallQueue':
            try:
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
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

        # Rules
        if e_type in ['User', 'CallQueue', 'Site', 'Department']:
            try:
                rules = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        if not r.get('enabled'): continue
                        
                        rtype = r.get('type', 'Custom')
                        rname = r.get('name', 'Rule')
                        action = r.get('callHandlingAction')
                        
                        logic_desc = ""
                        if rtype == 'BusinessHours':
                            rname = "Business Hours"
                            # Helper renders actual times
                            sched = get_schedule_summary(ext_id)
                            logic_desc = f"<b>{rname}</b><br/>{sched}"
                        elif rtype == 'Custom':
                            # Helper extracts conditions
                            conds = format_custom_rule(r)
                            logic_desc = f"<b>Custom: {clean_text(rname)}</b><br/>{conds}"
                        else:
                            if rtype == 'AfterHours': rname = "After Hours"
                            logic_desc = f"<b>{rname}</b>"

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
                            if rtype in ['BusinessHours', 'Custom']:
                                lid = f"log_{node_counter}"; node_counter += 1
                                # Hexagon logic node
                                graph_lines.append(f'{lid}{{"{clean_text(logic_desc)}"}}:::logicStyle')
                                graph_lines.append(f'{nid} --> {lid}')
                                _trace(target, lid, "Matches", new_hist)
                            else:
                                _trace(target, nid, rname, new_hist)
                        else:
                            detail = action
                            if action == 'PlayAnnouncementOnly': detail = "Play Announcement"
                            iid = f"cfg_{node_counter}"; node_counter += 1
                            graph_lines.append(f'{iid}["{clean_text(logic_desc)}<br/>Action: {detail}"]:::logicStyle')
                            graph_lines.append(f'{nid} -.-> {iid}')
            except Exception as e:
                print(f"Rule processing error: {e}")

        # IVR
        if e_type == 'IvrMenu':
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
