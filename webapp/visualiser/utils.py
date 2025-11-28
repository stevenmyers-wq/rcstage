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
            # Force cache bust
            final_url = endpoint
            if ("call-queues" in endpoint or "answering-rule" in endpoint or "extension" in endpoint) and "?" not in endpoint:
                final_url = f"{endpoint}?_={int(time.time())}"
            elif "?" in endpoint:
                final_url = f"{endpoint}&_={int(time.time())}"

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
            # Always ask for Detailed View
            info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}?view=Detailed")
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
        info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{s_num}?view=Detailed")
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
        if not action or action == 'AgentQueue': return "Ring Members"
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

    def extract_target_from_transfer(self, transfer_obj):
        """Extract target extension ID from various transfer object formats"""
        if not transfer_obj: return None
        if isinstance(transfer_obj, list):
            for t in transfer_obj:
                target = self.extract_target_from_transfer(t)
                if target: return target
        if isinstance(transfer_obj, dict):
            if transfer_obj.get('extension'):
                ext = transfer_obj['extension']
                if ext.get('id'): return str(ext['id'])
                if ext.get('extensionNumber'): 
                    return self.get_extension_id_by_number(ext['extensionNumber'])
            if transfer_obj.get('phoneNumber'):
                return f"ext_{transfer_obj['phoneNumber']}"
        return None

    def process_call_queue_advanced_rules(self, ext_id, nid, history):
        try:
            bh_rule = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule")
            if bh_rule and not bh_rule.get('errorCode'):
                action = bh_rule.get('callHandlingAction')
                # Only draw line if it's NOT just ringing the queue (avoid redundancy)
                if not action or action == 'AgentQueue':
                    pass
                else:
                    targets = self.extract_queue_targets_from_rule(bh_rule, 'Business Hours')
                    for target_id, label in targets:
                        if target_id and target_id not in history:
                            self.trace(target_id, nid, label, history, True)
            
            ah_rule = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/after-hours-rule")
            if ah_rule and not ah_rule.get('errorCode'):
                targets = self.extract_queue_targets_from_rule(ah_rule, 'After Hours')
                for target_id, label in targets:
                    if target_id and target_id not in history:
                        self.trace(target_id, nid, label, history, True)
        except Exception as e:
            print(f"Error processing advanced queue rules for {ext_id}: {e}")

    def extract_queue_targets_from_rule(self, rule, rule_name):
        targets = []
        try:
            if rule.get('queue', {}).get('transfer'):
                transfers = rule['queue']['transfer']
                if not isinstance(transfers, list): transfers = [transfers]
                for t in transfers:
                    action = t.get('action', 'Transfer')
                    if action == 'HoldTimeExpiration': action = "Hold Time Expired"
                    if action == 'MaxCallers': action = "Queue Full"
                    target = self.extract_target_from_transfer(t)
                    if target:
                        targets.append((target, f"{rule_name}: {action}"))
            
            if rule.get('unconditionalForwarding'):
                target = self.extract_target_from_transfer(rule['unconditionalForwarding'])
                if target:
                    targets.append((target, f"{rule_name} - Forward"))
            
            if rule.get('voicemail'):
                targets.append((f"vm_{rule.get('extension', {}).get('id', 'unknown')}", 
                              f"{rule_name} - Voicemail"))
        except Exception as e:
            print(f"Error extracting targets from rule: {e}")
        return targets

    def trace(self, ext_id, parent_id=None, link_label="", history=None, is_active=True):
        if history is None: history = []
        
        arrow_code = "-->" if is_active else "-.->"
        clean_lbl = self.clean_text(link_label)
        link_syntax = f'-- "{clean_lbl}" -->' if (is_active and clean_lbl) else (f'-. "{clean_lbl}" .->' if clean_lbl else arrow_code)

        if ext_id in history:
            if ext_id in self.node_map and parent_id:
                self.graph_lines.append(f'{parent_id} -.-> {self.node_map[ext_id]}')
            return
        
        is_vm = str(ext_id).startswith("vm_")
        if not is_vm and ext_id in self.node_map:
            if parent_id: 
                self.graph_lines.append(f'{parent_id} {link_syntax} {self.node_map[ext_id]}')
            return

        nid = f"n{self.node_counter}"
        self.node_counter += 1
        if not is_vm: self.node_map[ext_id] = nid
        new_hist = history + [ext_id]

        if str(ext_id).startswith("ext_"):
            lbl = f"[External]<br/><b>{ext_id.replace('ext_', '')}</b>"
            self.graph_lines.append(f'{nid}["{lbl}"]:::siteStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')
            return

        if is_vm:
            self.graph_lines.append(f'{nid}(("🛑 Voicemail")):::termStyle')
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

        extra_html = ""
        overflow_targets = [] 

        if e_type == 'CallQueue':
            try:
                # A. Schedule
                q_schedule = self.get_schedule_summary(ext_id)
                if q_schedule and q_schedule != "24/7 (Default)":
                    extra_html += f"<hr/><b>🕒 Schedule:</b><br/>{q_schedule}"

                # B. Agents
                m_resp = self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    m_names = []
                    for m in m_resp['records'][:6]:
                        mi = self.get_extension_info(m['id'])
                        if mi: m_names.append(f"- {self.clean_text(mi.get('name'))}")
                    if len(m_resp['records']) > 6: m_names.append(f"<i>... {len(m_resp['records'])-6} more</i>")
                    if m_names:
                        extra_html += f"<hr/><b>👥 Agents:</b><br/>" + "<br/>".join(m_names)

                # C. Settings (DEEP MERGE FLATTENER)
                if ext_id not in self.queue_settings_cache:
                    q_settings = {}
                    # 1. Queue Endpoint
                    q1 = self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}")
                    if q1: q_settings.update(q1)
                    
                    # 2. Extension Endpoint (Deep View)
                    q2 = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}?view=Detailed")
                    if q2:
                        # Extract root keys
                        for k in ['transfer', 'unconditionalForwarding', 'maxWaitTime', 'maxWaitTimeAction']:
                            if q2.get(k) is not None: q_settings[k] = q2[k]
                        # Extract nested keys (THE FIX)
                        if q2.get('callQueueInfo'):
                            for k, v in q2['callQueueInfo'].items():
                                q_settings[k] = v

                    self.queue_settings_cache[ext_id] = q_settings
                
                q = self.queue_settings_cache.get(ext_id)
                if q:
                    max_wait = q.get('maxWaitTime')
                    max_callers = q.get('maxCallers')
                    wait_action = q.get('maxWaitTimeAction', 'Unknown')
                    
                    if wait_action == 'Unknown' or wait_action == 'FixedWaitTime':
                        wait_action = "Ring Members"

                    # Format
                    wait_txt = "Unknown"
                    if max_wait is not None: wait_txt = f"{max_wait}s" if max_wait > 0 else "0s (Immediate)"
                    
                    callers_txt = "Unknown"
                    if max_callers is not None: callers_txt = str(max_callers)

                    extra_html += f"<hr/><b>⚙️ Config:</b><br/>Wait: {wait_txt}<br/>Max Callers: {callers_txt}<br/>Action: {wait_action}"

                    # Targets
                    target_id = self.extract_target_from_transfer(q.get('transfer'))
                    if not target_id: target_id = self.extract_target_from_transfer(q.get('unconditionalForwarding'))
                    if not target_id: target_id = self.extract_target_from_transfer(q.get('missedCall'))

                    if target_id:
                        lbl = "Immediate Overflow" if max_wait == 0 else f"Overflow (> {max_wait}s)"
                        overflow_targets.append((target_id, lbl))
                    else:
                        # Debugging Info if Missing
                        if wait_action not in ['Voicemail', 'Ring Members', 'Unknown']:
                            # List keys found in the MERGED object
                            debug_keys = list(q.keys())
                            extra_html += f"<br/>⚠️ <i>Target Missing</i><br/><span style='font-size:8px'>Keys: {', '.join(debug_keys)[:50]}...</span>"

                try:
                    overflow_resp = self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/overflow-settings")
                    if overflow_resp and overflow_resp.get('enabled') and overflow_resp.get('items'):
                        for item in overflow_resp['items']:
                            if item.get('id'):
                                overflow_targets.append((item['id'], "Queue Overflow"))
                except: pass

            except Exception as e: print(f"Queue Data Error: {e}")

        # Draw Node
        name_txt = self.clean_text(info.get('name'))
        num_txt = info.get('extensionNumber', '')
        final_label = f"[{e_type}] {name_txt}<br/>Ext: {num_txt}{extra_html}"
        
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        self.graph_lines.append(f'{nid}["{final_label}"]:::{style}')
        
        if parent_id: self.graph_lines.append(f'{parent_id} {link_syntax} {nid}')

        # Trace Children
        for target, lbl in overflow_targets:
            self.trace(target, nid, lbl, new_hist)
            
        if e_type == 'CallQueue':
            self.process_call_queue_advanced_rules(ext_id, nid, new_hist)

        if e_type in ['User', 'Site', 'Department']:
            try:
                rules = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=true")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        is_active = r.get('enabled', True)
                        status_txt = "" if is_active else " (Inactive)"
                        link_arrow = "-->" if is_active else "-.->"
                        node_style_class = "logicStyle" if is_active else "inactiveStyle"

                        rtype = r.get('type', 'Custom')
                        rname = r.get('name', 'Rule')
                        action = r.get('callHandlingAction')
                        
                        if rtype == 'BusinessHours' and (not action or action == 'AgentQueue'):
                            continue

                        logic_text = ""
                        if rtype == 'BusinessHours':
                            logic_text = f"<b>Business Hours</b>{status_txt}<br/>{self.get_schedule_summary(ext_id)}"
                        elif rtype == 'Custom':
                            logic_text = f"<b>{self.clean_text(rname)}</b>{status_txt}<br/>{self.format_custom_rule(r)}"
                        else:
                            if rtype == 'AfterHours': rname = "After Hours"
                            logic_text = f"<b>{self.clean_text(rname)}</b>{status_txt}"

                        target = self.extract_target_from_transfer(r.get('transfer'))
                        if not target: target = self.extract_target_from_transfer(r.get('unconditionalForwarding'))
                        
                        if action == 'ForwardCalls' and not target:
                            fwd_rules = r.get('forwarding', {}).get('rules', [])
                            for fr in fwd_rules:
                                for fn in fr.get('forwardingNumbers', []):
                                    if fn.get('phoneNumber'):
                                        target = f"ext_{fn['phoneNumber']}"
                                        break
                                if target: break
                        if action == 'TakeMessagesOnly' and not target: target = f"vm_{ext_id}"

                        if target:
                            lid = f"log_{self.node_counter}"; self.node_counter += 1
                            self.graph_lines.append(f'{lid}{"{{"}{self.clean_text(logic_text)}{"}}"}:::{node_style_class}')
                            self.graph_lines.append(f'{nid} {link_arrow} {lid}')
                            self.trace(target, lid, "Matches", new_hist, is_active)
                        else:
                            if action != 'AgentQueue':
                                iid = f"cfg_{self.node_counter}"; self.node_counter += 1
                                det = action or "Ring Members"
                                if action == 'PlayAnnouncementOnly': det = "Play Announcement"
                                self.graph_lines.append(f'{iid}["{self.clean_text(logic_text)}<br/>Action: {det}"]:::{node_style_class}')
                                self.graph_lines.append(f'{nid} {link_arrow} {iid}')

            except Exception as e: print(f"Rule Error: {e}")

        if e_type == 'IvrMenu':
            try:
                ivr = self.log_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr and ivr.get('actions'):
                    for act in ivr['actions']:
                        key = act.get('input', '?')
                        target = self.extract_target_from_transfer(act)
                        if target:
                            self.trace(target, nid, f"Key {key}", new_hist)
            except: pass

    def generate(self, start_ext_id):
        self.graph_lines = [
            '---', 'title: Call Flow Diagram', '---', 'graph LR',
            'classDef siteStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20',
            'classDef ivrStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1',
            'classDef queueStyle fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100',
            'classDef userStyle fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121',
            'classDef logicStyle fill:#fff,stroke:#7b1fa2,stroke-width:1px,stroke-dasharray: 5 5,color:#4a148c,font-size:12px',
            'classDef inactiveStyle fill:#f5f5f5,stroke:#9e9e9e,stroke-width:1px,stroke-dasharray: 2 2,color:#757575,font-size:12px,font-style:italic',
            'classDef infoStyle fill:#fff,stroke:#b0bec5,stroke-width:1px,stroke-dasharray: 2 2,color:#37474f,font-size:11px',
            'classDef errorStyle fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#c62828',
            'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5',
            'classDef termStyle fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#c62828,rx:10,ry:10'
        ]
        try:
            self.trace(start_ext_id)
        except Exception as e:
            self.graph_lines.append(f'error_node["⚠️ Generator Error: {str(e)}"]:::errorStyle')
        
        graph_str = "\n".join(self.graph_lines)
        if not graph_str.strip(): graph_str = "graph LR\nError[No Data Generated]"
        return graph_str, self.request_logs

def generate_mermaid_flow(start_ext_id):
    tracer = CallFlowTracer()
    return tracer.generate(start_ext_id)
