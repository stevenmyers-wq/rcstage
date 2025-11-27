# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

class CallFlowTracer:
    def __init__(self):
        self.extension_cache = {}
        self.schedule_cache = {}
        self.queue_settings_cache = {}
        self.request_logs = []
        self.graph_lines = []
        self.node_map = {}
        self.node_counter = 0
        self.ext_num_map = {} 

    def log_api_call(self, endpoint):
        """Wrapper to track API calls for the frontend debug log."""
        start = time.time()
        status = "SUCCESS"
        try:
            # Force cache bust for queues and rules
            final_url = endpoint
            if ("call-queues" in endpoint or "answering-rule" in endpoint) and "?" not in endpoint:
                final_url = f"{endpoint}?_={int(time.time())}"

            response = rc_api_call(final_url)
            duration = round((time.time() - start) * 1000, 2)
            
            if response is None: status = "EMPTY (None)"
            elif isinstance(response, dict) and 'errorCode' in response: 
                status = f"ERROR: {response.get('errorCode')}"
            
            self.request_logs.append({
                'method': 'GET',
                'url': final_url,
                'status': status,
                'duration': f"{duration}ms"
            })
            return response
        except Exception as e:
            self.request_logs.append({
                'method': 'GET',
                'url': endpoint,
                'status': f"EXCEPTION: {str(e)}",
                'duration': "0ms"
            })
            return None

    def get_extension_info(self, ext_id):
        if ext_id in self.extension_cache: return self.extension_cache[ext_id]
        if str(ext_id) in self.ext_num_map:
            real_id = self.ext_num_map[str(ext_id)]
            if real_id in self.extension_cache: return self.extension_cache[real_id]

        for i in range(3):
            info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                self.extension_cache[ext_id] = info
                if info.get('extensionNumber'):
                    self.ext_num_map[str(info['extensionNumber'])] = str(info['id'])
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
            time.sleep(0.1)
        return None

    def get_extension_id_by_number(self, ext_num):
        s_num = str(ext_num)
        if s_num in self.ext_num_map: return self.ext_num_map[s_num]
        info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{s_num}")
        if info and info.get('id'):
            self.extension_cache[str(info['id'])] = info
            self.ext_num_map[s_num] = str(info['id'])
            return str(info['id'])
        return None

    def clean_text(self, text):
        if not text: return ""
        return str(text).replace('"', "'").replace('#', '').strip()

    def parse_schedule(self, schedule_obj):
        if not schedule_obj: return "24/7 (Open)"
        try:
            output_lines = []
            if schedule_obj.get('weeklyRanges'):
                weekly_data = schedule_obj['weeklyRanges']
                normalized_items = []
                if isinstance(weekly_data, dict):
                    for day_key, periods in weekly_data.items():
                        if isinstance(periods, dict): periods = [periods]
                        for p in periods:
                            normalized_items.append({'day': day_key, 'from': p.get('from'), 'to': p.get('to')})
                elif isinstance(weekly_data, list):
                    for item in weekly_data:
                        normalized_items.append({'day': item.get('dayOfWeek', 'Unknown'), 'from': item.get('from'), 'to': item.get('to')})

                time_map = {}
                for item in normalized_items:
                    s_str = str(item.get('from') or '00:00').split(':')
                    e_str = str(item.get('to') or '23:59').split(':')
                    s_fmt = f"{s_str[0]}:{s_str[1]}" if len(s_str) >= 2 else "00:00"
                    e_fmt = f"{e_str[0]}:{e_str[1]}" if len(e_str) >= 2 else "23:59"
                    time_key = f"{s_fmt}-{e_fmt}"
                    day_raw = item.get('day', '???')
                    day_short = day_raw[:1].upper() + day_raw[1:3].lower()
                    if time_key not in time_map: time_map[time_key] = []
                    time_map[time_key].append(day_short)

                days_order = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                for t_str, days in time_map.items():
                    days.sort(key=lambda d: days_order.index(d) if d in days_order else 99)
                    d_label = ",".join(days)
                    if len(days) == 5 and 'Mon' in days and 'Fri' in days: d_label = "Mon-Fri"
                    elif len(days) == 7: d_label = "Everyday"
                    elif len(days) == 2 and 'Sat' in days and 'Sun' in days: d_label = "Weekends"
                    output_lines.append(f"{d_label}: {t_str}")

            if schedule_obj.get('ranges'):
                for r in schedule_obj['ranges']:
                    f = str(r.get('from', '')).replace('T', ' ')[:16]
                    t = str(r.get('to', '')).replace('T', ' ')[:16]
                    output_lines.append(f"{f} to {t}")

            if not output_lines: return "24/7 (Open)"
            return "<br/>".join(output_lines)
        except: return f"Raw: {str(schedule_obj)[:50]}"

    def get_schedule_summary(self, ext_id):
        if ext_id in self.schedule_cache: return self.schedule_cache[ext_id]
        try:
            resp = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
            if not resp or 'schedule' not in resp: return "24/7 (Default)"
            res = self.parse_schedule(resp['schedule'])
            self.schedule_cache[ext_id] = res
            return res
        except: return "Fetch Err"

    def format_custom_rule(self, rule):
        try:
            conds = []
            if rule.get('callers'):
                names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
                conds.append(f"From: {', '.join(names[:2])}" + (f"..." if len(names)>2 else ""))
            if rule.get('calledNumbers'):
                nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
                conds.append(f"To: {', '.join(nums)}")
            if rule.get('schedule'):
                sch_text = self.parse_schedule(rule['schedule'])
                if sch_text != "24/7 (Open)":
                    conds.append(f"<b>Time:</b> {sch_text}")
            return "<br/>".join(conds) if conds else "Matches All Calls"
        except: return "Complex Rule"

    def get_action_description(self, rule):
        action = rule.get('callHandlingAction')
        if action == 'TransferToExtension':
            ext = rule.get('transfer', {}).get('extension', {})
            return f"Transfer to Ext {ext.get('extensionNumber', '?')}"
        if action == 'UnconditionalForwarding':
            ph = rule.get('unconditionalForwarding', {}).get('phoneNumber')
            if ph: return f"Forward to {ph}"
            ext = rule.get('unconditionalForwarding', {}).get('extension', {})
            if ext: return f"Forward to Ext {ext.get('extensionNumber', '?')}"
        if action == 'ForwardCalls':
            fwd_rules = rule.get('forwarding', {}).get('rules', [])
            targets = []
            for r in fwd_rules:
                for n in r.get('forwardingNumbers', []):
                    if n.get('phoneNumber'): targets.append(n['phoneNumber'])
            if targets: return f"Forward to {', '.join(targets[:1])}" + ("..." if len(targets)>1 else "")
            return "Ring Devices"
        if action == 'TakeMessagesOnly': return "Send to Voicemail"
        if action == 'PlayAnnouncementOnly': return "Play Announcement"
        return action

    def resolve_target_from_rule(self, rule):
        """Helper to find target ID from any rule object."""
        target_id = None
        action = rule.get('callHandlingAction')
        
        # 1. TransferToExtension
        if action in ['TransferToExtension', 'ForwardToExtension']:
            if rule.get('transfer') and rule['transfer'].get('extension'):
                if rule['transfer']['extension'].get('id'):
                    target_id = rule['transfer']['extension']['id']
                elif rule['transfer']['extension'].get('extensionNumber'):
                    target_id = self.get_extension_id_by_number(rule['transfer']['extension']['extensionNumber'])

        # 2. UnconditionalForwarding
        elif action == 'UnconditionalForwarding':
            uf = rule.get('unconditionalForwarding', {})
            if uf.get('extension'):
                if uf['extension'].get('id'): target_id = uf['extension']['id']
                elif uf['extension'].get('extensionNumber'): target_id = self.get_extension_id_by_number(uf['extension']['extensionNumber'])
            elif uf.get('phoneNumber'):
                target_id = f"ext_{uf['phoneNumber']}"

        # 3. TakeMessagesOnly
        elif action == 'TakeMessagesOnly':
            # We need the extension ID this rule belongs to, passed separately?
            # For now return specific flag
            return "VOICEMAIL"

        return target_id

    def trace(self, ext_id, parent_id=None, link_label="", history=None, is_active=True):
        if history is None: history = []
        
        arrow_code = "-->" if is_active else "-.->"
        clean_lbl = self.clean_text(link_label)
        link_syntax = f'-- "{clean_lbl}" -->' if (is_active and clean_lbl) else (f'-. "{clean_lbl}" .->' if clean_lbl else arrow_code)

        if ext_id in history:
            if ext_id in self.node_map and parent_id:
                self.graph_lines.append(f'{parent_id} -.-> {self.node_map[ext_id]}')
            return
        if ext_id in self.node_map:
            if parent_id: 
                self.graph_lines.append(f'{parent_id} {link_syntax} {self.node_map[ext_id]}')
            return

        nid = f"n{self.node_counter}"
        self.node_counter += 1
        self.node_map[ext_id] = nid
        new_hist = history + [ext_id]

        # --- Node Creation ---
        if str(ext_id).startswith("ext_"):
            lbl = f"[External]<br/><b>{ext_id.replace('ext_', '')}</b>"
            self.graph_lines.append(f'{nid}["{lbl}"]:::siteStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')
            return

        if str(ext_id).startswith("vm_"):
            self.graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')
            return

        info = self.get_extension_info(ext_id)
        if not info:
            self.graph_lines.append(f'{nid}["[Unknown: {ext_id}]"]:::missingStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')
            return

        e_type = info.get('type', 'Unknown')
        if e_type == 'Department' and self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{self.clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        self.graph_lines.append(f'{nid}["{label}"]:::{style}')
        
        if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')

        # --- CALL QUEUE LOGIC (SPECIFIC RULES) ---
        if e_type == 'CallQueue':
            try:
                # 1. Agents
                m_resp = self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    m_list = []
                    for m in m_resp['records'][:6]:
                        mi = self.get_extension_info(m['id'])
                        if mi: m_list.append(f"- {self.clean_text(mi.get('name'))}")
                    if len(m_resp['records']) > 6: m_list.append(f"... {len(m_resp['records'])-6} more")
                    iid = f"info_{self.node_counter}"; self.node_counter += 1
                    self.graph_lines.append(f'{iid}["<b>Agents:</b><br/>{ "<br/>".join(m_list) }"]:::infoStyle')
                    self.graph_lines.append(f'{nid} -.-> {iid}')

                # 2. BUSINESS HOURS RULE (The missing link!)
                # We specifically check this endpoint because it often holds the "Overflow" IVR
                bh_rule = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule")
                if bh_rule:
                    target_id = self.resolve_target_from_rule(bh_rule)
                    if target_id == "VOICEMAIL": target_id = f"vm_{ext_id}"
                    
                    if target_id:
                        # This is the "Queue Overflow" or "Queue Logic" path
                        self.trace(target_id, nid, "Business Hours Routing", new_hist)
                    else:
                        # If no explicit transfer, it might just mean "Ring Agents"
                        pass

                # 3. AFTER HOURS RULE
                ah_rule = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/after-hours-rule")
                if ah_rule:
                    target_id = self.resolve_target_from_rule(ah_rule)
                    if target_id == "VOICEMAIL": target_id = f"vm_{ext_id}"
                    if target_id:
                        self.trace(target_id, nid, "After Hours Routing", new_hist)

            except Exception as e:
                print(f"Queue Rule Error: {e}")

        # --- GENERIC ROUTING RULES (Users, Sites, etc) ---
        # Note: We skip CallQueues here because we handled them specifically above
        if e_type in ['User', 'Site', 'Department']:
            try:
                rules = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=true")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        is_active = r.get('enabled', True)
                        status_txt = "" if is_active else " <i>(Inactive)</i>"
                        link_arrow = "-->" if is_active else "-.->"
                        
                        rname = r.get('name', 'Rule')
                        rtype = r.get('type', 'Custom')
                        target = self.resolve_target_from_rule(r)
                        if target == "VOICEMAIL": target = f"vm_{ext_id}"

                        if target:
                            self.trace(target, nid, f"{rname}{status_txt}", new_hist, is_active)
                        else:
                            # Non-transfer rule
                            action = r.get('callHandlingAction', 'Unknown')
                            iid = f"cfg_{self.node_counter}"; self.node_counter += 1
                            self.graph_lines.append(f'{iid}["{self.clean_text(rname)}<br/>Action: {action}"]:::infoStyle')
                            self.graph_lines.append(f'{nid} {link_arrow} {iid}')

            except Exception as e:
                print(f"Rule Error: {e}")

        # --- IVR ---
        if e_type == 'IvrMenu':
            try:
                ivr = self.log_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr and ivr.get('actions'):
                    for act in ivr['actions']:
                        key = act.get('input', '?')
                        if act.get('extension', {}).get('id'):
                            self.trace(act['extension']['id'], nid, f"Key {key}", new_hist)
                        elif act.get('phoneNumber'):
                            self.trace(f"ext_{act['phoneNumber']}", nid, f"Key {key}", new_hist)
            except: pass

    def generate(self, start_ext_id):
        self.graph_lines = [
            '---', 'title: Call Flow Diagram', '---', 'graph TD',
            'classDef siteStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20',
            'classDef ivrStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1',
            'classDef queueStyle fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100',
            'classDef userStyle fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121',
            'classDef logicStyle fill:#fff,stroke:#7b1fa2,stroke-width:1px,stroke-dasharray: 5 5,color:#4a148c,font-size:12px',
            'classDef inactiveStyle fill:#f5f5f5,stroke:#9e9e9e,stroke-width:1px,stroke-dasharray: 2 2,color:#757575,font-size:12px,font-style:italic',
            'classDef infoStyle fill:#fff,stroke:#b0bec5,stroke-width:1px,stroke-dasharray: 2 2,color:#37474f,font-size:11px',
            'classDef errorStyle fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#c62828',
            'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
        ]
        try:
            self.trace(start_ext_id)
        except Exception as e:
            self.graph_lines.append(f'error_node["⚠️ Generator Error: {str(e)}"]:::errorStyle')
        
        graph_str = "\n".join(self.graph_lines)
        if not graph_str.strip():
            graph_str = "graph TD\nError[No Data Generated]"
            
        return graph_str, self.request_logs

def generate_mermaid_flow(start_ext_id):
    tracer = CallFlowTracer()
    return tracer.generate(start_ext_id)
