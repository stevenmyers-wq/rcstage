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

def parse_schedule_safely(weekly_ranges):
    """
    Safely parses weekly ranges into a readable string.
    Guaranteed to return a string, never raises an exception.
    """
    if not weekly_ranges: return "24/7"
    
    try:
        # Grouping Logic
        groups = {}
        for item in weekly_ranges:
            # 1. Safe Time Extraction
            raw_from = item.get('from', '00:00')
            raw_to = item.get('to', '23:59')
            # Convert "09:00:00" -> "09:00"
            s = str(raw_from).split(':')
            e = str(raw_to).split(':')
            t_str = f"{s[0]}:{s[1]}-{e[0]}:{e[1]}"
            
            # 2. Day Extraction
            d = item.get('dayOfWeek', '???')[:3]
            
            if t_str not in groups: groups[t_str] = []
            groups[t_str].append(d)

        # Formatting Logic
        lines = []
        day_map = {'Sun':0, 'Mon':1, 'Tue':2, 'Wed':3, 'Thu':4, 'Fri':5, 'Sat':6}
        
        for t_str, days in groups.items():
            days.sort(key=lambda x: day_map.get(x, 99))
            d_lbl = ",".join(days)
            if len(days) == 5 and 'Mon' in days and 'Fri' in days: d_lbl = "Mon-Fri"
            if len(days) == 7: d_lbl = "Everyday"
            if len(days) == 2 and 'Sat' in days and 'Sun' in days: d_lbl = "Weekends"
            lines.append(f"{d_lbl}: {t_str}")
            
        return "<br/>".join(lines)
        
    except Exception:
        # Emergency Fallback: Just dump the raw values so the user sees *something*
        try:
            raw_lines = []
            for item in weekly_ranges:
                raw_lines.append(f"{item.get('dayOfWeek','')[:3]} {item.get('from','')} - {item.get('to','')}")
            return "<br/>".join(raw_lines)
        except:
            return "Schedule Data Error"

def get_schedule_summary(ext_id):
    if ext_id in schedule_cache: return schedule_cache[ext_id]
    try:
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        if not resp or 'schedule' not in resp: return "24/7"
        
        sched = resp['schedule']
        if not sched.get('weeklyRanges'): return "24/7"
        
        res = parse_schedule_safely(sched['weeklyRanges'])
        schedule_cache[ext_id] = res
        return res
    except: return "Sched Err"

def format_custom_rule(rule):
    try:
        conds = []
        # Caller ID
        if rule.get('callers'):
            names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
            txt = f"From: {', '.join(names[:2])}"
            if len(names) > 2: txt += f" (+{len(names)-2})"
            conds.append(txt)
        # Called Number
        if rule.get('calledNumbers'):
            nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
            conds.append(f"To: {', '.join(nums)}")
        # Schedule
        sch = rule.get('schedule', {})
        if sch.get('weeklyRanges'):
            conds.append(f"<b>Time:</b> {parse_schedule_safely(sch['weeklyRanges'])}")
        if sch.get('ranges'):
            conds.append(f"<b>Dates:</b> {len(sch['ranges'])} Specific Ranges")
            
        return "<br/>".join(conds) if conds else "Matches All"
    except: return "Custom Rule (Err)"

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
        'classDef errorStyle fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#c62828',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    node_map = {}
    node_counter = 0

    def _trace(ext_id, parent_id=None, link_label="", history=None):
        nonlocal node_counter
        if history is None: history = []
        
        # 1. Loop Prevention
        if ext_id in history:
            if ext_id in node_map and parent_id:
                graph_lines.append(f'{parent_id} -- "{clean_text(link_label)} (Loop)" --> {node_map[ext_id]}')
            return
        if ext_id in node_map:
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {node_map[ext_id]}')
            return

        # 2. Node Generation
        if str(ext_id).startswith("ext_"): # External
            nid = f"n{node_counter}"; node_counter+=1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}["[External]<br/><b>{ext_id.replace("ext_","")}</b>"]:::siteStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return
            
        if str(ext_id).startswith("vm_"): # VM
            nid = f"n{node_counter}"; node_counter+=1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        # Fetch Info
        info = get_extension_info(ext_id)
        nid = f"n{node_counter}"; node_counter+=1
        node_map[ext_id] = nid
        new_hist = history + [ext_id]

        if not info:
            graph_lines.append(f'{nid}["[Unknown ID: {ext_id}]"]:::missingStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        if e_type == 'Department' and rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        graph_lines.append(f'{nid}["{label}"]:::{style}')
        if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')

        # 3. Queue Members (Agents)
        if e_type == 'CallQueue':
            try:
                # Sleep briefly to prevent rate limit triggers
                time.sleep(0.05)
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    m_list = []
                    for m in m_resp['records'][:6]:
                        mi = get_extension_info(m['id'])
                        if mi: m_list.append(f"- {clean_text(mi.get('name'))}")
                    if len(m_resp['records']) > 6: m_list.append(f"... {len(m_resp['records'])-6} more")
                    if m_list:
                        iid = f"info_{node_counter}"; node_counter+=1
                        graph_lines.append(f'{iid}["<b>Agents:</b><br/>{ "<br/>".join(m_list) }"]:::infoStyle')
                        graph_lines.append(f'{nid} -.-> {iid}')
            except: pass

        # 4. Routing Rules - ROBUST BLOCK
        if e_type in ['User', 'CallQueue', 'Site', 'Department']:
            try:
                time.sleep(0.05) # Rate limit safety
                rules_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false"
                rules = rc_api_call(rules_url)
                
                # Check for API Failure
                if rules is None:
                    # Draw error node so we KNOW it failed
                    err_id = f"err_{node_counter}"; node_counter+=1
                    graph_lines.append(f'{err_id}["⚠️ Error Loading Rules"]:::errorStyle')
                    graph_lines.append(f'{nid} -.-> {err_id}')
                
                elif rules.get('records'):
                    for r in rules['records']:
                        # INDIVIDUAL TRY-CATCH per rule to prevent total crash
                        try:
                            if not r.get('enabled'): continue
                            
                            rtype = r.get('type', 'Custom')
                            rname = r.get('name', 'Rule')
                            action = r.get('callHandlingAction')
                            
                            logic_desc = ""
                            if rtype == 'BusinessHours':
                                rname = "Business Hours"
                                logic_desc = f"<b>{rname}</b><br/>{get_schedule_summary(ext_id)}"
                            elif rtype == 'Custom':
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
                                    lid = f"log_{node_counter}"; node_counter+=1
                                    # Use safe description
                                    graph_lines.append(f'{lid}{{"{clean_text(logic_desc)}"}}:::logicStyle')
                                    graph_lines.append(f'{nid} --> {lid}')
                                    _trace(target, lid, "Matches", new_hist)
                                else:
                                    _trace(target, nid, rname, new_hist)
                            else:
                                detail = action
                                if action == 'PlayAnnouncementOnly': detail = "Play Announcement"
                                iid = f"cfg_{node_counter}"; node_counter+=1
                                graph_lines.append(f'{iid}["{clean_text(logic_desc)}<br/>Action: {detail}"]:::logicStyle')
                                graph_lines.append(f'{nid} -.-> {iid}')
                                
                        except Exception as e:
                            print(f"Skipping single rule due to error: {e}")

            except Exception as e:
                print(f"Failed to fetch rules for {ext_id}: {e}")
                err_id = f"err_{node_counter}"; node_counter+=1
                graph_lines.append(f'{err_id}["⚠️ System Error: Rules"]:::errorStyle')
                graph_lines.append(f'{nid} -.-> {err_id}')

        # 5. IVR
        if e_type == 'IvrMenu':
            try:
                time.sleep(0.05)
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
