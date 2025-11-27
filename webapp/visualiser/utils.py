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
            
            if not response: status = "EMPTY"
            elif 'errorCode' in response: status = f"ERROR: {response.get('errorCode')}"
            
            self.request_logs.append({
                'method': 'GET',
                'url': endpoint, # Ensuring key is exactly 'url'
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
        
        # Retry logic
        for i in range(3):
            info = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                self.extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']:
                return {'type': 'Unknown', 'name': 'Deleted', 'extensionNumber': '???'}
            time.sleep(0.1)
        return None

    def parse_schedule(self, schedule_obj):
        """Robustly parses schedule objects into string format."""
        try:
            if not schedule_obj: return "24/7"
            
            # 1. Standard Weekly
            if schedule_obj.get('weeklyRanges'):
                groups = {}
                for item in schedule_obj['weeklyRanges']:
                    s = str(item.get('from', '00:00')).split(':')
                    e = str(item.get('to', '23:59')).split(':')
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

            # 2. Specific Ranges
            if schedule_obj.get('ranges'):
                return f"Specific Dates ({len(schedule_obj['ranges'])} ranges)"
            
            return "24/7 (Open)"
        except:
            return "Schedule Data"

    def get_schedule_summary(self, ext_id):
        if ext_id in self.schedule_cache: return self.schedule_cache[ext_id]
        try:
            resp = self.log_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
            if not resp or 'schedule' not in resp: return "24/7"
            res = self.parse_schedule(resp['schedule'])
            self.schedule_cache[ext_id] = res
            return res
        except: return "Sched Err"

    def format_custom_rule(self, rule):
        try:
            conds = []
            if rule.get('callers'):
                names = [c.get('name', c.get('callerId', '?')) for c in rule['callers']]
                conds.append(f"From: {', '.join(names[:2])}" + (f" (+{len(names)-2})" if len(names)>2 else ""))
            if rule.get('calledNumbers'):
                nums = [n.get('phoneNumber', '?') for n in rule['calledNumbers']]
                conds.append(f"To: {', '.join(nums)}")
            if rule.get('schedule'):
                conds.append(f"<b>Time:</b> {self.parse_schedule(rule['schedule'])}")
            return "<br/>".join(conds) if conds else "Matches All"
        except: return "Complex Rule"

    def clean_text(self, text):
        if not text: return ""
        return str(text).replace('"', "'").replace('#', '').strip()

    def trace(self, ext_id, parent_id=None, link_label="", history=None):
        if history is None: history = []
        
        # Loop Check
        if ext_id in history:
            if ext_id in self.node_map and parent_id:
                self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)} (Loop)" --> {self.node_map[ext_id]}')
            return
        if ext_id in self.node_map:
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {self.node_map[ext_id]}')
            return

        # Node ID Generation
        nid = f"n{self.node_counter}"
        self.node_counter += 1
        self.node_map[ext_id] = nid
        new_hist = history + [ext_id]

        # --- TYPES ---
        
        # 1. External
        if str(ext_id).startswith("ext_"):
            lbl = f"[External]<br/><b>{ext_id.replace('ext_', '')}</b>"
            self.graph_lines.append(f'{nid}["{lbl}"]:::siteStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        # 2. Voicemail
        if str(ext_id).startswith("vm_"):
            self.graph_lines.append(f'{nid}(("[Voicemail]")):::userStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        # 3. Standard
        info = self.get_extension_info(ext_id)
        if not info:
            self.graph_lines.append(f'{nid}["[Unknown: {ext_id}]"]:::missingStyle')
            if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')
            return

        e_type = info.get('type', 'Unknown')
        # Check Department -> Queue
        if e_type == 'Department' and self.log_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
            e_type = 'CallQueue'

        label = f"[{e_type}]<br/><b>{self.clean_text(info.get('name'))}</b><br/>{info.get('extensionNumber', '')}"
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(e_type, 'userStyle')
        self.graph_lines.append(f'{nid}["{label}"]:::{style}')
        
        if parent_id: self.graph_lines.append(f'{parent_id} -- "{self.clean_text(link_label)}" --> {nid}')

        # --- DETAILS ---

        # Queue Agents
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

        # Rules
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
                            # Non-transfer
                            iid = f"cfg_{self.node_counter}"; self.node_counter += 1
                            det = action
                            if action == 'PlayAnnouncementOnly': det = "Play Announcement"
                            self.graph_lines.append(f'{iid}["{self.clean_text(logic)}<br/>Action: {det}"]:::logicStyle')
                            self.graph_lines.append(f'{nid} -.-> {iid}')
            except Exception as e:
                print(f"Rule Error: {e}")

        # IVR
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
        # Reset and Start
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
        
        # Ensure we always return a string, never None
        graph_str = "\n".join(self.graph_lines)
        if not graph_str.strip():
            graph_str = "graph TD\nError[No Data Generated]"
            
        return graph_str, self.request_logs

# Bridge for routes.py
def generate_mermaid_flow(start_ext_id):
    tracer = CallFlowTracer()
    return tracer.generate(start_ext_id)
