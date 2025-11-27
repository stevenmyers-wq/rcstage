# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

class CallFlowTracer:
    def __init__(self):
        self.extension_cache = {}
        self.schedule_cache = {}
        self.request_logs = []
        self.graph_lines = []
        self.node_map = {}
        self.node_counter = 0

    def log_api_call(self, endpoint):
        """Wrapper to track API calls for the frontend debug log."""
        start = time.time()
        status = "SUCCESS"
        try:
            response = rc_api_call(endpoint)
            duration = round((time.time() - start) * 1000, 2)
            
            if response is None: status = "EMPTY (None)"
            elif isinstance(response, dict) and 'errorCode' in response: 
                status = f"ERROR: {response.get('errorCode')}"
            
            self.request_logs.append({
                'method': 'GET',
                'url': endpoint,
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
        
        for i in range(3):
            info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                self.extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
            time.sleep(0.1)
        return None

    def clean_text(self, text):
        if not text: return ""
        # Remove quotes and special chars that break Mermaid syntax
        return str(text).replace('"', "'").replace('#', '').strip()

    # ==========================================
    #  TRANSPARENT PARSER (No Hiding Errors)
    # ==========================================
    def parse_schedule(self, schedule_obj):
        """
        Parses schedule. If it fails, DUMPS RAW JSON so we can see the data.
        """
        if not schedule_obj: return "24/7 (Open)"
        
        try:
            output_lines = []
            
            # 1. Weekly Ranges
            if schedule_obj.get('weeklyRanges'):
                time_map = {}
                for item in schedule_obj['weeklyRanges']:
                    # Extract raw values
                    raw_s = item.get('from', '00:00')
                    raw_e = item.get('to', '23:59')
                    
                    # Handle "09:00:00" vs "09:00"
                    s_str = str(raw_s).split(':')
                    e_str = str(raw_e).split(':')
                    
                    # Safe formatting
                    s_fmt = f"{s_str[0]}:{s_str[1]}" if len(s_str) >= 2 else "00:00"
                    e_fmt = f"{e_str[0]}:{e_str[1]}" if len(e_str) >= 2 else "23:59"
                    
                    time_key = f"{s_fmt}-{e_fmt}"
                    day_short = item.get('dayOfWeek', '???')[:3]
                    
                    if time_key not in time_map: time_map[time_key] = []
                    time_map[time_key].append(day_short)

                # Sort and textify
                days_order = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                for t_str, days in time_map.items():
                    days.sort(key=lambda d: days_order.index(d) if d in days_order else 99)
                    d_label = ",".join(days)
                    if len(days) == 5 and 'Mon' in days and 'Fri' in days: d_label = "Mon-Fri"
                    elif len(days) == 7: d_label = "Everyday"
                    elif len(days) == 2 and 'Sat' in days and 'Sun' in days: d_label = "Weekends"
                    output_lines.append(f"{d_label}: {t_str}")

            # 2. Ranges (Holidays)
            if schedule_obj.get('ranges'):
                for r in schedule_obj['ranges']:
                    f = r.get('from', '?').replace('T', ' ')[:16]
                    t = r.get('to', '?').replace('T', ' ')[:16]
                    output_lines.append(f"{f} to {t}")

            if not output_lines:
                # If object exists but empty ranges, dump it to be safe
                return f"Empty Sched: {json.dumps(schedule_obj)}"
                
            return "<br/>".join(output_lines)

        except Exception as e:
            # DATA DUMP ON ERROR - Show the user exactly what failed
            try:
                raw_json = json.dumps(schedule_obj, indent=0).replace('"', "'")
                return f"FORMAT ERR: {str(e)}<br/>Data: {raw_json[:100]}..." 
            except:
                return f"CRITICAL DATA ERR: {str(e)}"

    def get_schedule_summary(self, ext_id):
        if ext_id in self.schedule_cache: return self.schedule_cache[ext_id]
        
        try:
            # Fetch
            resp = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
            
            # 1. API Failure Check
            if resp is None: 
                return "API: No Response (Null)"
            
            if 'errorCode' in resp:
                return f"API Err: {resp.get('errorCode')}"
                
            # 2. Data Check
            if 'schedule' not in resp: 
                return f"No 'schedule' key. Keys: {list(resp.keys())}"
            
            sched = resp['schedule']
            res = self.parse_schedule(sched)
            self.schedule_cache[ext_id] = res
            return res
            
        except Exception as e:
            # Reveal the exact python error
            return f"Py Err: {str(e)}"

    def format_custom_rule(self, rule):
        try:
            conds = []
            # Caller ID
            if rule.get('callers'):
                names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
                conds.append(f"From: {', '.join(names[:2])}" + (f"..." if len(names)>2 else ""))
            
            # Called Number
            if rule.get('calledNumbers'):
                nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
                conds.append(f"To: {', '.join(nums)}")
            
            # Schedule
            if rule.get('schedule'):
                sch_text = self.parse_schedule(rule['schedule'])
                if sch_text != "24/7 (Open)":
                    conds.append(f"<b>Time:</b> {sch_text}")

            return "<br/>".join(conds) if conds else "Matches All Calls"
        except Exception as e:
            return f"Rule Err: {str(e)}"

    def trace(self, ext_id, parent_id=None, link_label="", history=None):
        if history is None: history = []
        
        if ext_id in history:
            if ext_id in self.node_map and parent_id:
                self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)} (Loop)" --> {self.node_map[ext_id]}')
            return
        if ext_id in self.node_map:
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {self.node_map[ext_id]}')
            return

        nid = f"n{self.node_counter}"
        self.node_counter += 1
        self.node_map[ext_id] = nid
        new_hist = history + [ext_id]

        # --- Types ---
        if str(ext_id).startswith("ext_"):
            lbl = f"[External]<br/><b>{ext_id.replace('ext_', '')}</b>"
            self.graph_lines.append(f'{nid}["{lbl}"]:::siteStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        if str(ext_id).startswith("vm_"):
            self.graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        info = self.get_extension_info(ext_id)
        if not info:
            self.graph_lines.append(f'{nid}["[Unknown: {ext_id}]"]:::missingStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        if e_type == 'Department' and self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{self.clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        self.graph_lines.append(f'{nid}["{label}"]:::{style}')
        
        if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')

        # --- Details ---
        if e_type == 'CallQueue':
            try:
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
            except: pass

        # --- Rules ---
        if e_type in ['User', 'CallQueue', 'Site', 'Department']:
            try:
                rules = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules and rules.get('records'):
                    for r in rules['records']:
                        if not r.get('enabled'): continue
                        
                        rtype = r.get('type', 'Custom')
                        rname = r.get('name', 'Rule')
                        action = r.get('callHandlingAction')
                        
                        logic = ""
                        if rtype == 'BusinessHours':
                            # Logic: Fetch schedule using strict parser
                            logic = f"<b>Business Hours</b><br/>{self.get_schedule_summary(ext_id)}"
                        elif rtype == 'Custom':
                            logic = f"<b>Custom: {self.clean_text(rname)}</b><br/>{self.format_custom_rule(r)}"
                        elif rtype == 'AfterHours':
                            logic = f"<b>After Hours</b>"
                        else:
                            logic = f"<b>{self.clean_text(rname)}</b>"

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
                                lid = f"log_{self.node_counter}"; self.node_counter += 1
                                self.graph_lines.append(f'{lid}{{"{self.clean_text(logic)}"}}:::logicStyle')
                                self.graph_lines.append(f'{nid} --> {lid}')
                                self.trace(target, lid, "Matches", new_hist)
                            else:
                                self.trace(target, nid, rname, new_hist)
                        else:
                            iid = f"cfg_{self.node_counter}"; self.node_counter += 1
                            det = action
                            if action == 'PlayAnnouncementOnly': det = "Play Announcement"
                            self.graph_lines.append(f'{iid}["{self.clean_text(logic)}<br/>Action: {det}"]:::logicStyle')
                            self.graph_lines.append(f'{nid} -.-> {iid}')
            except Exception as e:
                print(f"Rule Error: {e}")

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
