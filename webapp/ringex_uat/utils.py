from webapp.rc_api import rc_api_call

# =============================================================================
# FAIL-SAFE JSON EXTRACTORS
# Prevents 'NoneType' crashes when RingCentral API returns `null`
# =============================================================================

def safe_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    return val if isinstance(val, dict) else {}

def safe_list(d, key):
    if not isinstance(d, dict): return []
    val = d.get(key)
    return val if isinstance(val, list) else []

def get_testable_extensions():
    """Fetches base call flows for the UI dropdown. Excludes standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not isinstance(response, dict): return []
    records = safe_list(response, 'records')
    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [
        {"id": ext.get('id'), "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext.get('type')}
        for ext in records if isinstance(ext, dict) and ext.get('type') in valid_types
    ]
    return sorted(entities, key=lambda x: x['name'])


class UATGenerator:
    """Forensic, data-driven crawler that translates raw API JSON directly into UAT cases."""
    
    def __init__(self, start_ext_id, start_ext_name, start_ext_number, start_ext_type):
        self.ext_map = self._build_ext_map()
        self.queue_to_process = [{
            "id": str(start_ext_id),
            "name": start_ext_name,
            "ext": start_ext_number,
            "type": start_ext_type,
            "path": "Primary Flow"
        }]
        self.processed_ids = set()
        self.test_cases = []
        self.counter = 1

    def _build_ext_map(self):
        """Builds a cached dictionary of all extensions to resolve IDs into actual Names/Numbers."""
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
        m = {}
        if isinstance(resp, dict):
            records = safe_list(resp, 'records')
            for e in records:
                if isinstance(e, dict) and 'id' in e:
                    m[str(e['id'])] = {
                        'name': e.get('name', 'Unknown'),
                        'ext': e.get('extensionNumber', 'N/A'),
                        'type': e.get('type', 'Unknown')
                    }
        return m

    def add_case(self, category, scenario, action, expected):
        self.test_cases.append({
            "test_id": f"UAT-{self.counter:04d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        self.counter += 1

    def _resolve_target(self, rule_obj, action_name="Unknown Action"):
        """Forensically digs through standard RingCentral routing objects to find the true destination."""
        if not isinstance(rule_obj, dict):
            return f"Action: {action_name}", None
            
        # 1. Check for Extension Transfer
        transfer = safe_dict(rule_obj, 'transfer')
        if not transfer: 
            # Some endpoints return transfer as a list
            t_list = safe_list(rule_obj, 'transfer')
            if t_list and isinstance(t_list[0], dict): transfer = t_list[0]
            
        ext = safe_dict(transfer, 'extension')
        if ext and ext.get('id'):
            tid = str(ext.get('id'))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}")
            text = self.ext_map.get(tid, {}).get('ext', '')
            return f"Transfer -> {tname} (Ext {text})", tid
            
        # 2. Check for Voicemail Transfer
        voicemail = safe_dict(rule_obj, 'voicemail')
        recip = safe_dict(voicemail, 'recipient')
        if recip and recip.get('id'):
            tid = str(recip.get('id'))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}")
            return f"Voicemail -> {tname}", tid
            
        # 3. Check for External Forwarding
        forward = safe_dict(rule_obj, 'unconditionalForwarding')
        if not forward:
            f_list = safe_list(rule_obj, 'unconditionalForwarding')
            if f_list and isinstance(f_list[0], dict): forward = f_list[0]
            
        if forward and forward.get('phoneNumber'):
            return f"External Forward -> {forward.get('phoneNumber')}", None
            
        # 4. Fallback to the configured action string
        extracted_action = rule_obj.get('callHandlingAction', action_name)
        if extracted_action == "TakeMessagesReturnToGreeting":
            return "Voicemail / Default Operator", None
        return f"Action: {extracted_action}", None

    def _extract_rule_conditions(self, rule):
        """Accurately parses the exact triggers for a custom rule from the JSON arrays."""
        conditions = []
        callers = safe_list(rule, 'callers')
        if callers:
            c_ids = [c.get('callerId', c.get('name', 'Unknown')) for c in callers if isinstance(c, dict)]
            conditions.append(f"Caller ID is {', '.join(c_ids)}")
            
        called = safe_list(rule, 'calledNumbers')
        if called:
            n_ids = [n.get('phoneNumber', 'Unknown') for n in called if isinstance(n, dict)]
            conditions.append(f"Dialed Number is {', '.join(n_ids)}")
            
        if safe_dict(rule, 'schedule'):
            conditions.append("Matches Custom Schedule")
            
        return " AND ".join(conditions) if conditions else "Specific Configured Condition"

    def process(self):
        while self.queue_to_process:
            curr = self.queue_to_process.pop(0)
            cid = curr['id']
            cname = curr['name']
            cext = curr['ext']
            ctype = curr['type']
            cpath = curr['path']

            # Prevent infinite routing loops
            if cid in self.processed_ids: continue
            self.processed_ids.add(cid)

            prefix = f"[{cname}] "
            path_str = f"[Path: {cpath}]\n" if cpath != "Primary Flow" else ""

            # ---------------------------------------------------------
            # 1. PHONE NUMBERS (Strict matching against extension ID)
            # ---------------------------------------------------------
            dids = []
            ph_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/phone-number', method='GET', raise_error=False)
            if isinstance(ph_resp, dict):
                for r in safe_list(ph_resp, 'records'):
                    if isinstance(r, dict) and r.get('usageType') == 'DirectNumber':
                        ext_obj = safe_dict(r, 'extension')
                        if str(ext_obj.get('id')) == str(cid) and r.get('phoneNumber'):
                            dids.append(r.get('phoneNumber'))

            if cpath == "Primary Flow":
                self.add_case(f"{prefix}Integration", "Internal Routing", f"Dial extension {cext} internally.", f"Call successfully connects to {cname}.")
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}Integration", f"External Routing (DID)", f"Dial the assigned DID {did} from an external mobile device.", f"Call successfully connects to {cname} via PSTN.")

            # ---------------------------------------------------------
            # 2. BUSINESS HOURS
            # ---------------------------------------------------------
            bh_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/business-hours', method='GET', raise_error=False)
            bh_str = "24/7 (Always Open)"
            
            if isinstance(bh_resp, dict) and safe_dict(bh_resp, 'schedule'):
                sched = safe_dict(bh_resp, 'schedule')
                weekly = safe_dict(sched, 'weeklyRanges')
                if weekly:
                    days = []
                    for d, t_list in weekly.items():
                        if isinstance(t_list, list) and len(t_list) > 0 and isinstance(t_list[0], dict):
                            days.append(f"{d[:3]} {t_list[0].get('from')}-{t_list[0].get('to')}")
                    if days: bh_str = ", ".join(days)
            elif not isinstance(bh_resp, dict) or not bh_resp:
                # Inherit from account if Queue has no custom schedule
                acc_bh = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
                if isinstance(acc_bh, dict) and safe_dict(acc_bh, 'schedule'):
                    sched = safe_dict(acc_bh, 'schedule')
                    weekly = safe_dict(sched, 'weeklyRanges')
                    if weekly:
                        days = []
                        for d, t_list in weekly.items():
                            if isinstance(t_list, list) and len(t_list) > 0 and isinstance(t_list[0], dict):
                                days.append(f"{d[:3]} {t_list[0].get('from')}-{t_list[0].get('to')}")
                        if days: bh_str = ", ".join(days)

            self.add_case(f"{prefix}Routing", "Business Hours (In-Hours)", f"{path_str}Initiate call during Open Hours: [{bh_str}].", f"Call follows standard Business Hours routing for {cname}.")

            # ---------------------------------------------------------
            # 3. ANSWERING RULES (Custom, After Hours, Queue Overflows)
            # ---------------------------------------------------------
            ar_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules = safe_list(ar_resp, 'records') if isinstance(ar_resp, dict) else []
            
            queue_settings = {}
            has_intro = False
            has_after_hours = False

            for rule in rules:
                if not isinstance(rule, dict) or not rule.get('enabled', False): continue
                rtype = rule.get('type')

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    cond_str = self._extract_rule_conditions(rule)
                    tname, tid = self._resolve_target(rule, rule.get('callHandlingAction'))
                    
                    self.add_case(f"{prefix}Routing", f"Custom Rule Trigger: {rname}", f"{path_str}Initiate call where: {cond_str}.", f"Rule intercepts call. Executes -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}'"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    has_after_hours = True
                    tname, tid = self._resolve_target(rule, rule.get('callHandlingAction'))
                    
                    self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours: [{bh_str}].", f"Executes After Hours logic. Routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours Routing"})

                # --- QUEUE SETTINGS ---
                elif rtype == 'BusinessHours' and ctype == 'Department':
                    queue_settings = safe_dict(rule, 'queue')
                    greetings = safe_list(rule, 'greetings')
                    if any(isinstance(g, dict) and g.get('type') == 'Introductory' for g in greetings):
                        has_intro = True

            # If no After Hours rule explicitly defined, note the default override
            if not has_after_hours and bh_str != "24/7 (Always Open)":
                 self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours: [{bh_str}].", f"Follows default account After Hours logic.")

            # ---------------------------------------------------------
            # 4. CALL QUEUE EXHAUSTIVE TESTING
            # ---------------------------------------------------------
            if ctype == 'Department':
                q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/call-queue-info', method='GET', raise_error=False)
                if not isinstance(q_info, dict): q_info = {}
                
                # Merge configurations from /answering-rule and /call-queue-info
                tmode = q_info.get('transferMode') or queue_settings.get('transferMode') or 'Simultaneous'
                ag_timeout = q_info.get('agentTimeout') or queue_settings.get('agentTimeout') or 0
                wrap_up = q_info.get('wrapUpTime') or queue_settings.get('wrapUpTime') or 0
                hold_time = queue_settings.get('holdTime') or q_info.get('holdTime') or 0
                max_callers = queue_settings.get('maxCallers') or q_info.get('maxCallers') or 0
                int_per = q_info.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod') or 0

                # --- Queue Experience ---
                if has_intro:
                    self.add_case(f"{prefix}Queue Experience", "Introductory Greeting", f"{path_str}Place call to {cname}.", "Intro Greeting plays fully before agent ringing begins.")
                self.add_case(f"{prefix}Queue Experience", "Connecting Audio", f"Remain on hold in {cname}.", "Configured Hold Music plays cleanly.")
                if int_per and int(int_per) > 0:
                    self.add_case(f"{prefix}Queue Experience", f"Interrupt Audio ({int_per}s)", f"Remain on hold for at least {int(int_per) + 5} seconds.", "Hold music pauses, interrupt prompt plays, then hold music resumes.")

                # --- Agent Management ---
                self.add_case(f"{prefix}Agent Tests", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON. Place test call.", f"Agent's device rings. Queue Name '{cname}' prepends Caller ID.")
                self.add_case(f"{prefix}Agent Tests", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place test call.", "Agent's device does NOT ring. Call smoothly hunts to next available agent.")
                if wrap_up and int(wrap_up) > 0:
                    self.add_case(f"{prefix}Agent Tests", f"Wrap-Up (ACW) Timer ({wrap_up}s)", f"Agent completes a call. Immediately place another.", f"Agent enters Wrap-Up status and does NOT ring again until {wrap_up}s expires.")

                # --- Distribution Logic ---
                self.add_case(f"{prefix}Distribution", f"Routing: {tmode}", f"Ensure agents are 'Available'. Place call.", f"Call distributes based strictly on {tmode} logic.")
                if str(tmode).lower() != 'simultaneous' and ag_timeout and int(ag_timeout) > 0:
                    self.add_case(f"{prefix}Distribution", f"Ring Timeout ({ag_timeout}s)", f"Targeted agent ignores call for {ag_timeout} seconds.", "Timer expires. Call immediately hunts to the next available agent.")

                # --- Boundaries & Overflows ---
                h_act = queue_settings.get('holdTimeExpirationAction', 'Unknown')
                h_name, h_id = self._resolve_target(queue_settings, h_act) 
                
                m_act = queue_settings.get('maxCallersAction', 'Unknown')
                m_name, m_id = self._resolve_target(queue_settings, m_act)

                self.add_case(f"{prefix}Queue Boundaries", "Zero Agents Available", f"Log ALL agents out of {cname} or set DND. Initiate call.", f"Call bypasses queue entirely and triggers Overflow -> {h_name}.")

                if hold_time and int(hold_time) > 0:
                    h_mins = int(hold_time) // 60 if int(hold_time) >= 60 else int(hold_time)
                    lbl = f"{h_mins} minutes" if int(hold_time) >= 60 else f"{hold_time} seconds"
                    self.add_case(f"{prefix}Queue Boundaries", f"Max Wait Time Limit ({lbl})", f"Remain on hold in {cname} for exactly {lbl}.", f"Timer expires. Call executes -> {h_name}.")
                    if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": f"Wait Time Overflow"})
                else:
                    self.add_case(f"{prefix}Queue Boundaries", "Unlimited Wait Time", f"Remain on hold in {cname} for 10+ minutes.", "No wait time limit configured. Call remains in queue indefinitely.")

                if max_callers and int(max_callers) > 0:
                    self.add_case(f"{prefix}Queue Boundaries", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {cname} with {max_callers} calls. Dial call #{int(max_callers) + 1}.", f"Call #{int(max_callers) + 1} is blocked and executes -> {m_name}.")
                    if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": f"Max Callers Overflow"})
                else:
                    self.add_case(f"{prefix}Queue Boundaries", "Unlimited Queue Capacity", f"Place multiple concurrent calls into {cname}.", "No capacity limit configured. Calls are not rejected based on volume.")

                zero_name = "Default Operator"
                vmail = safe_dict(queue_settings, 'voicemail')
                recip = safe_dict(vmail, 'recipient')
                if recip and recip.get('id'):
                    z_id = str(recip.get('id'))
                    zero_name = f"Voicemail of {self.ext_map.get(z_id, {}).get('name', z_id)}"
                
                # Check if Voicemail is enabled or if there's an operator fallback
                if vmail or queue_settings.get('transfer'):
                    self.add_case(f"{prefix}Queue Boundaries", "Zero-Out (DTMF '0')", f"While listening to {cname} hold music, press '0' on the dialpad.", f"Call escapes the queue and routes to: {zero_name}.")
                else:
                    self.add_case(f"{prefix}Queue Boundaries", "Zero-Out Disabled", f"While listening to {cname} hold music, press '0' on the dialpad.", "Input is gracefully ignored. Call remains in queue.")

            # ---------------------------------------------------------
            # 5. IVR MENU TESTING
            # ---------------------------------------------------------
            elif ctype == 'IvrMenu':
                ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False)
                if not isinstance(ivr_info, dict): ivr_info = {}
                
                prompt = safe_dict(ivr_info, 'prompt')
                ptext = prompt.get('text', 'Configured Audio File')
                self.add_case(f"{prefix}IVR Tests", "Greeting Playback", f"Dial {cname}.", f"Prompt plays clearly: '{ptext}'.")
                
                actions = safe_list(ivr_info, 'actions')
                if actions:
                    for act in actions:
                        if not isinstance(act, dict): continue
                        key = act.get('input', '')
                        if not key: continue
                        
                        a_type = act.get('action', 'Unknown')
                        tname, tid = self._resolve_target(act, a_type)
                        self.add_case(f"{prefix}IVR Routing", f"Key Mapping: '{key}'", f"{path_str}Listen to prompt and press '{key}'.", f"Registers DTMF. Executes [{a_type}] -> {tname}.")
                        
                        if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Key '{key}'"})
                
                self.add_case(f"{prefix}IVR Boundaries", "Invalid Key Press", f"Press an unassigned key in {cname}.", "System plays 'Invalid entry' prompt and replays menu.")

        self.add_case("Global Validation", "Call Logs Generation", "Log into Admin Portal > Analytics > Call Logs.", "All test calls are accurately reflected, showing correct Caller ID, target extensions, duration, and final result.")

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Entry point for the UI router."""
    generator = UATGenerator(extension_id, extension_name, extension_number, extension_type)
    generator.process()
    return generator.test_cases
