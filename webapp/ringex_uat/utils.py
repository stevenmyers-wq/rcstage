from webapp.rc_api import rc_api_call

# =============================================================================
# FAIL-SAFE JSON EXTRACTORS
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
    """Forensic, data-driven crawler that translates raw API JSON directly into holistic UAT cases."""
    
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

    def _resolve_target(self, rule_obj, action_type):
        """Forensically maps specific overflow/transfer actions to their true configured destinations."""
        if not isinstance(rule_obj, dict):
            return "Unknown Destination", None
            
        if action_type in ['TransferToExtension', 'Bypass']:
            tid = str(safe_dict(rule_obj, 'transfer').get('extension', {}).get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension ID {tid}")
            text = self.ext_map.get(tid, {}).get('ext', '')
            return f"Transfer -> {tname} (Ext {text})", tid
            
        elif action_type in ['TakeMessagesReturnToGreeting', 'Voicemail']:
            tid = str(safe_dict(rule_obj, 'voicemail').get('recipient', {}).get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension ID {tid}")
            text = self.ext_map.get(tid, {}).get('ext', '')
            return f"Voicemail -> {tname} (Ext {text})", tid
            
        elif action_type == 'UnconditionalForwarding':
            fw = safe_dict(rule_obj, 'unconditionalForwarding')
            num = fw.get('phoneNumber') or str(fw)
            return f"External Forward -> {num}", None
            
        elif action_type == 'PlayAnnouncementOnly':
            return "Play Announcement & Disconnect", None
            
        elif action_type == 'WaitPrimaryMembers':
            return "Wait for Primary Members", None
            
        return f"Action: {action_type}", None

    def _extract_rule_conditions(self, rule):
        """Accurately parses the exact triggers for a custom rule from the JSON arrays."""
        conditions = []
        callers = safe_list(rule, 'callers')
        if callers:
            c_ids = [c.get('callerId', c.get('name', '')) for c in callers if isinstance(c, dict) and (c.get('callerId') or c.get('name'))]
            if c_ids: conditions.append(f"Caller ID is {', '.join(c_ids)}")
            
        called = safe_list(rule, 'calledNumbers')
        if called:
            n_ids = [n.get('phoneNumber', '') for n in called if isinstance(n, dict) and n.get('phoneNumber')]
            if n_ids: conditions.append(f"Dialed Number is {', '.join(n_ids)}")
            
        sched = safe_dict(rule, 'schedule')
        if sched and sched.get('ref') != 'BusinessHours':
            conditions.append("Matches Custom Time Schedule")
            
        return " AND ".join(conditions) if conditions else "Specific Configured Condition"

    def process(self):
        while self.queue_to_process:
            curr = self.queue_to_process.pop(0)
            cid = curr['id']
            cname = curr['name']
            cext = curr['ext']
            ctype = curr['type']
            cpath = curr['path']

            if cid in self.processed_ids: continue
            self.processed_ids.add(cid)

            prefix = f"[{cname}] "
            path_str = f"[Path: {cpath}]\n" if cpath != "Primary Flow" else ""

            # ---------------------------------------------------------
            # 1. CONNECTIVITY (Strict DID Validation)
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
                self.add_case(f"{prefix}Integration", "Internal Routing", f"Dial extension {cext} internally.", f"Call connects successfully to {cname} without dead air.")
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}Integration", f"External Routing (DID)", f"Dial the assigned DID {did} from a mobile phone.", f"Call connects via the PSTN to {cname} with high-quality, two-way audio.")
                else:
                    self.add_case(f"{prefix}Integration", "External Routing (No DID)", f"Dial the Main Company Number and enter extension {cext}.", f"Call successfully routes to {cname}.")

            # ---------------------------------------------------------
            # 2. SCHEDULE BOUNDARIES
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

            self.add_case(f"{prefix}Routing", "Business Hours (In-Hours)", f"{path_str}Initiate a call during Open Hours: [{bh_str}].", f"Call follows standard Business Hours routing path.")

            # ---------------------------------------------------------
            # 3. DETAILED ANSWERING RULES (Custom, After Hours, Overflows)
            # ---------------------------------------------------------
            ar_summary_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules_summary = safe_list(ar_summary_resp, 'records') if isinstance(ar_summary_resp, dict) else []
            
            has_after_hours = False

            for rule_sum in rules_summary:
                if not isinstance(rule_sum, dict) or not rule_sum.get('enabled', False): continue
                rule_id = rule_sum.get('id')
                rtype = rule_sum.get('type')

                # EXPLICITLY FETCH THE DEEP PAYLOAD TO EXPOSE QUEUE LIMITS AND CUSTOM CONDITIONS
                rule = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule/{rule_id}', method='GET', raise_error=False)
                if not isinstance(rule, dict): rule = rule_sum

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    cond_str = self._extract_rule_conditions(rule)
                    action = rule.get('callHandlingAction', 'Unknown Action')
                    tname, tid = self._resolve_target(rule, action)
                    
                    self.add_case(f"{prefix}Routing", f"Custom Rule Trigger: {rname}", f"{path_str}Initiate a call matching conditions: {cond_str}.", f"Rule intercepts call. Executes [{action}] -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}'"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    has_after_hours = True
                    action = rule.get('callHandlingAction', 'Unknown Action')
                    tname, tid = self._resolve_target(rule, action)
                    
                    self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate a call OUTSIDE of Open Hours.", f"Executes After Hours logic [{action}] -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours Routing"})

                # --- QUEUE BEHAVIOR (BusinessHours contains the Queue Limits) ---
                elif rtype == 'BusinessHours' and ctype == 'Department':
                    q = safe_dict(rule, 'queue')
                    
                    # Caller Experience
                    greetings = safe_list(rule, 'greetings')
                    if any(isinstance(g, dict) and g.get('type') == 'Introductory' for g in greetings):
                        self.add_case(f"{prefix}Caller Experience", "Introductory Greeting", f"{path_str}Place a call to the queue.", "Configured Intro Greeting plays fully before agent ringing begins.")
                    
                    self.add_case(f"{prefix}Caller Experience", "Connecting Audio (Hold Music)", f"Remain in the queue while waiting.", "The configured hold music plays cleanly without distortion.")
                    
                    int_per = q.get('holdAudioInterruptionPeriod', 0)
                    if int_per > 0:
                        self.add_case(f"{prefix}Caller Experience", f"Wait Announcement ({int_per}s)", f"Remain on hold in {cname} for > {int_per} seconds.", f"At exactly {int_per}s, music pauses, wait announcement plays, and music resumes.")

                    # Overflows and Boundaries (Max Wait & Max Callers)
                    hold_time = q.get('holdTime', 0)
                    if hold_time > 0:
                        h_mins = hold_time // 60 if hold_time >= 60 else hold_time
                        lbl = f"{h_mins} minutes" if hold_time >= 60 else f"{hold_time} seconds"
                        
                        h_act = q.get('holdTimeExpirationAction', 'Unknown')
                        h_name, h_id = self._resolve_target(rule, h_act)
                        
                        self.add_case(f"{prefix}Queue Boundaries", f"Max Wait Time Limit ({lbl})", f"Remain on hold in {cname} for exactly {lbl}.", f"Timer expires. Call is forcefully removed and executes [{h_act}] -> {h_name}.")
                        if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": "Max Wait Time Overflow"})
                    
                    max_callers = q.get('maxCallers', 0)
                    if max_callers > 0:
                        m_act = q.get('maxCallersAction', 'Unknown')
                        m_name, m_id = self._resolve_target(rule, m_act)
                        
                        self.add_case(f"{prefix}Queue Boundaries", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {cname} with {max_callers} active calls. Dial call #{max_callers + 1}.", f"Call #{max_callers + 1} breaches capacity limit. Instantly executes [{m_act}] -> {m_name}.")
                        if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": "Max Callers Overflow"})

                    # Zero-Out always routes to the Voicemail recipient in RC queues
                    z_name, _ = self._resolve_target(rule, 'Voicemail')
                    self.add_case(f"{prefix}Queue Boundaries", "Zero-Out (Press 0)", f"While listening to {cname} hold music, press '0'.", f"Call safely escapes the queue and routes to Voicemail Recipient / Operator -> {z_name}.")

                    # Agent & Distribution Parameters
                    tmode = q.get('transferMode', 'Simultaneous')
                    ag_timeout = q.get('agentTimeout', 0)
                    wrap_up = q.get('wrapUpTime', 0)

                    self.add_case(f"{prefix}Agent & Distribution", f"Routing: {tmode}", "Ensure multiple agents are 'Available'. Place a call.", f"Call distributes based exactly on {tmode} logic.")
                    if str(tmode).lower() != 'simultaneous' and ag_timeout > 0:
                        self.add_case(f"{prefix}Agent & Distribution", f"Agent Ring Timeout ({ag_timeout}s)", f"Targeted agent lets the call ring without answering for {ag_timeout} seconds.", "Timer expires. Call immediately drops from Agent 1 and rings next available.")

                    self.add_case(f"{prefix}Agent & Distribution", "Queue Opt-In/DND", "Agent toggles 'Accept Queue Calls' ON, then OFF.", "Device rings when ON, and smoothly bypasses the agent when OFF.")
                    self.add_case(f"{prefix}Agent & Distribution", "Active Call Decline", "While call is ringing an agent, agent actively clicks 'Decline'.", "Ringing stops immediately for that agent. Call hunts to next available agent.")

                    if wrap_up > 0:
                        self.add_case(f"{prefix}Agent & Distribution", f"Wrap-Up (ACW) Timer ({wrap_up}s)", "Agent finishes a queue call and hangs up. Place new call.", f"Agent is in Wrap-Up state and does NOT ring again until {wrap_up}s expires.")

                    # Generic Call Handling verification for Queues
                    self.add_case(f"{prefix}Call Handling", "Queue Call Holding", "Agent answers queue call and places caller on hold.", "Caller hears agent hold music. Call is successfully retrieved.")
                    self.add_case(f"{prefix}Call Handling", "Warm & Blind Transfers", "Agent answers queue call and initiates a transfer to an internal extension.", "Call successfully connects and routes to the target extension.")

            if not has_after_hours and bh_str != "24/7 (Always Open)":
                 self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours.", f"Follows default account-level After Hours routing logic.")

            # ---------------------------------------------------------
            # 4. IVR MENU TESTING
            # ---------------------------------------------------------
            if ctype == 'IvrMenu':
                ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False)
                if not isinstance(ivr_info, dict): ivr_info = {}
                
                prompt = safe_dict(ivr_info, 'prompt')
                ptext = prompt.get('text', 'Configured Audio File')
                self.add_case(f"{prefix}Caller Experience", "Greeting Playback", f"Dial {cname}.", f"Prompt plays clearly: '{ptext}'. Wording matches approved script.")
                self.add_case(f"{prefix}Caller Experience", "Barge-In (Interruptibility)", f"While the greeting is actively playing, press a valid menu key.", "IVR registers DTMF tone immediately and routes the call without forcing caller to listen to full message.")
                
                actions = safe_list(ivr_info, 'actions')
                if actions:
                    for act in actions:
                        if not isinstance(act, dict): continue
                        key = act.get('input', '')
                        if not key: continue
                        
                        a_type = act.get('action', 'Unknown')
                        tname, tid = self._resolve_target({'transfer': act, 'voicemail': act, 'unconditionalForwarding': act}, a_type)
                        self.add_case(f"{prefix}IVR Routing", f"Key Mapping: '{key}'", f"{path_str}Listen to prompt and press '{key}'.", f"System processes input and executes [{a_type}] -> {tname}.")
                        
                        if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Key '{key}'"})
                
                self.add_case(f"{prefix}IVR Boundaries", "Dial-By-Extension", f"While in the IVR, enter a known internal user's 3 or 4-digit extension.", "If enabled, IVR intercepts the string and transfers call to the user.")
                self.add_case(f"{prefix}IVR Boundaries", "Invalid Key Press", f"Press an unassigned key (e.g., '9' or '#').", "System plays 'Invalid entry' prompt and replays menu.")
                self.add_case(f"{prefix}IVR Boundaries", "Timeout (No Input)", f"Listen to the entire prompt and provide no DTMF input.", "System times out, replays menu, and eventually executes default timeout routing.")

        self.add_case("Global Validation", "Post-Call & Voicemail", "Trigger any tested routing scenario that routes to Voicemail. Leave a test message.", "The correct Voicemail greeting plays. Voicemail audio is recorded and delivered accurately to inbox.")
        self.add_case("Global Validation", "Analytics & Logging", "Log into the Admin Portal and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct originating Caller ID, target extensions, duration, and final routing result.")

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Entry point for the UI router."""
    generator = UATGenerator(extension_id, extension_name, extension_number, extension_type)
    generator.process()
    return generator.test_cases
