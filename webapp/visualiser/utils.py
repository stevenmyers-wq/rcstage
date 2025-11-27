# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

# Global Caches
extension_cache = {}
schedule_cache = {}

# Local Log Tracker (Reset per request)
request_logs = []

def logged_rc_call(endpoint):
    """
    Wrapper around rc_api_call that forces logging for the frontend debug panel.
    Fixes the "[GET] undefined" issue.
    """
    start = time.time()
    try:
        response = rc_api_call(endpoint)
        duration = round((time.time() - start) * 1000, 2)
        
        status = "SUCCESS"
        if not response: status = "EMPTY"
        elif 'errorCode' in response: status = f"ERROR: {response.get('errorCode')}"
        
        # Log to our global list
        request_logs.append({
            'method': 'GET',
            'url': endpoint,
            'status': status,
            'duration': f"{duration}ms"
        })
        return response
    except Exception as e:
        request_logs.append({
            'method': 'GET',
            'url': endpoint,
            'status': f"EXCEPTION: {str(e)}"
        })
        return None

def get_extension_info(ext_id):
    if ext_id in extension_cache: return extension_cache[ext_id]
    for i in range(3):
        try:
            info = logged_rc_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
        except: time.sleep(0.5)
    return None

def parse_schedule_safely(schedule_obj):
    """
    Aggressive parser: Returns readable text if possible, RAW text if not.
    Never raises an exception.
    """
    try:
        # 1. Weekly Ranges (Standard Mon-Fri)
        if schedule_obj.get('weeklyRanges'):
            weekly = schedule_obj['weeklyRanges']
            groups = {}
            for item in weekly:
                raw_from = item.get('from', '00:00')
                raw_to = item.get('to', '23:59')
                # Clean up "09:00:00" -> "09:00"
                s = str(raw_from).split(':')
                e = str(raw_to).split(':')
                t_str = f"{s[0]}:{s[1]}-{e[0]}:{e[1]}"
                
                d = item.get('dayOfWeek', '???')[:3]
                if t_str not in groups: groups[t_str] = []
                groups[t_str].append(d)
                
            lines = []
            day_map = {'Sun':0, 'Mon':1, 'Tue':2, 'Wed':3, 'Thu':4, 'Fri':5, 'Sat':6}
            for t, days in groups.items():
                days.sort(key=lambda x: day_map.get(x, 99))
                d_lbl = ",".join(days)
                if len(days) == 5 and 'Mon' in days and 'Fri' in days: d_lbl = "Mon-Fri"
                if len(days) == 7: d_lbl = "Everyday"
                lines.append(f"{d_lbl}: {t}")
            return "<br/>".join(lines)

        # 2. Ranges (Specific Dates / Holidays)
        if schedule_obj.get('ranges'):
            ranges = schedule_obj['ranges']
            lines = []
            for r in ranges[:3]: # Show first 3
                f = r.get('from', '').split('T')[0]
                t = r.get('to', '').split('T')[0]
                lines.append(f"{f} to {t}")
            if len(ranges) > 3: lines.append(f"(+{len(ranges)-3} more)")
            return "<br/>".join(lines)
            
        return "24/7 (Open)"
    except Exception as e:
        # FALLBACK: Return raw string of object keys so we see something
        return f"Raw: {str(schedule_obj)[:50]}..."

def get_schedule_summary(ext_id):
    if ext_id in schedule_cache: return schedule_cache[ext_id]
    try:
        # Always fetch fresh
        resp = logged_rc_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        
        if not resp: return "24/7 (Default)"
        if 'schedule' not in resp: return "24/7"
        
        sched = resp['schedule']
        # If both are missing, it's open
        if not sched.get('weeklyRanges') and not sched.get('ranges'):
            return "24/7 (Open)"
            
        res = parse_schedule_safely(sched)
        schedule_cache[ext_id] = res
        return res
    except: 
        return "Fetch Error"

def format_custom_rule(rule):
    try:
        conds = []
        if rule.get('callers'):
            names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
            conds.append(f"From: {', '.join(names)}")
            
        if rule.get('calledNumbers'):
            nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
            conds.append(f"To: {', '.join(nums)}")
            
        # Parse Rule-Specific Schedule
        if rule.get('schedule'):
            sch_text = parse_schedule_safely(rule['schedule'])
            if sch_text != "24/7 (Open)":
                conds.append(f"<b>Time:</b> {sch_text}")

        return "<br/>".join(conds) if conds else "Matches All"
    except: return "Complex Rule"

def clean_text(text):
    if not text: return ""
    return str(text).replace('"', "'").replace('#', '').strip()

def generate_mermaid_flow(start_ext_id):
    # Reset logs for this run
    global request_logs
    request_logs = []
    
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
        
        # Cycle Check
        if ext_id in history:
            if ext_id in node_map and parent_id:
                graph_lines.append(f'{parent_id} -- "{clean_text(link_label)} (Loop)" --> {node_map[ext_id]}')
            return
        if ext_id in node_map:
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {node_map[ext_id]}')
            return

        # --- Node Creation ---
        if str(ext_id).startswith("ext_"):
            nid = f"n{node_counter}"; node_counter+=1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}["[External]<br/><b>{ext_id.replace("ext_","")}</b>"]:::siteStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return
        if str(ext_id).startswith("vm_"):
            nid = f"n{node_counter}"; node_counter+=1
            node_map[ext_id] = nid
            graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        info = get_extension_info(ext_id)
        nid = f"n{node_counter}"; node_counter+=1
        node_map[ext_id] = nid
        new_hist = history + [ext_id]

        if not info:
            graph_lines.append(f'{nid}["[Unknown: {ext_id}]"]:::missingStyle')
            if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        if e_type == 'Department' and logged_rc_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        graph_lines.append(f'{nid}["{label}"]:::{style}')
        if parent_id: graph_lines.append(f'{parent_id} -- "{clean_text(link_label)}" --> {nid}')

        # --- Queue Agents ---
        if e_type == 'CallQueue':
            try:
                m_resp = logged_rc_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
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

        # --- Rules ---
        if e_type in ['User', 'CallQueue', 'Site', 'Department']:
            try:
                # Use Detailed View
                rules = logged_rc_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                
                if rules and rules.get('records'):
                    for r in rules['records']:
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
                            print(f"Skipping bad rule: {e}")

            except Exception as e:
                err_id = f"err_{node_counter}"; node_counter+=1
                graph_lines.append(f'{err_id}["⚠️ Rules Error: {str(e)[:30]}"]:::errorStyle')
                graph_lines.append(f'{nid} -.-> {err_id}')

        # --- IVR ---
        if e_type == 'IvrMenu':
            try:
                ivr = logged_rc_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr and ivr.get('actions'):
                    for act in ivr['actions']:
                        key = act.get('input', '?')
                        if act.get('extension', {}).get('id'):
                            _trace(act['extension']['id'], nid, f"Key {key}", new_hist)
                        elif act.get('phoneNumber'):
                            _trace(f"ext_{act['phoneNumber']}", nid, f"Key {key}", new_hist)
            except: pass

    _trace(start_ext_id)
    # Return Tuple (Graph, Logs)
    return "\n".join(graph_lines), request_logs
