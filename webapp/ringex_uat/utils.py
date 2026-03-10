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
        self.phone_numbers_map = self._build_phone_numbers_map()
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

    def _build_phone_numbers_map(self):
        """Fetches all account phone numbers using v2 API to accurately map DIDs to extensions."""
        resp = rc_api_call('/restapi/v2/accounts/~/phone-numbers', params={'perPage': 1000}, raise_error=False)
        num_map = {}
        if isinstance(resp, dict):
            records = safe_list(resp, 'records')
            for r in records:
                if not isinstance(r, dict): continue
                ext_obj = safe_dict(r, 'extension')
                ext_id = str(ext_obj.get('id', ''))
                phone_number = r.get('phoneNumber')
                
                if ext_id and phone_number:
                    if ext_id not in num_map:
                        num_map[ext_id] = []
                    if phone_number not in num_map[ext_id]:
                        num_map[ext_id].append(phone_number)
        return num_map

    def add_case(self, category, scenario, action, expected):
        """Safely appends exactly 4 arguments to the test case list."""
        self.test_cases.append({
            "test_id": f"UAT-{self.counter:03d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        self.counter += 1

    def _resolve_target(self, rule_obj, action_type=None):
        """Forensically maps specific overflow/transfer actions and RC backend Enums to human destinations."""
        if not isinstance(rule_obj, dict):
            return f"Action: {action_type or 'Unknown'}", None
            
        act = action_type or rule_obj.get('callHandlingAction') or 'Unknown Action'
            
        if act in ['TransferToExtension', 'Bypass', 'Transfer']:
            transfer = safe_dict(rule_obj, 'transfer')
            if not transfer: 
                t_list = safe_list(rule_obj, 'transfer')
                if t_list and isinstance(t_list[0], dict): transfer = t_list[0]
                
            tid = str(safe_dict(transfer, 'extension').get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}") if tid else "Unknown Extension"
            text = self.ext_map.get(tid, {}).get('ext', '')
            ext_str = f" (Ext {text})" if text else ""
            return f"Internal Extension: {tname}{ext_str}", tid
            
        elif act in ['TakeMessagesReturnToGreeting', 'Voicemail']:
            voicemail = safe_dict(rule_obj, 'voicemail')
            recip = safe_dict(voicemail, 'recipient')
            tid = str(recip.get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}") if tid else "System Default"
            return f"Voicemail Inbox of {tname}", tid
            
        elif act == 'UnconditionalForwarding':
            forward = safe_dict(rule_obj, 'unconditionalForwarding')
            if not forward:
                f_list = safe_list(rule_obj, 'unconditionalForwarding')
                if f_list and isinstance(f_list[0], dict): forward = f_list[0]
            num = forward.get('phoneNumber', 'Unknown Number') if isinstance(forward, dict) else 'Unknown Number'
            return f"External Forwarding Number ({num})", None
            
        elif act == 'PlayAnnouncementOnly':
            return "Play Announcement & Disconnect", None
            
        elif act == 'WaitPrimaryMembers':
            return "Wait for Primary Members", None
            
        elif act == 'AgentQueue':
            return "Queue Agents (Standard Queue Distribution)", None
            
        elif act == 'ForwardCalls':
            return "User's Configured Devices & Forwarding Numbers", None
            
        elif act == 'SharedLines':
            return "Shared Lines Group", None
            
        return f"{act}", None

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
            # 1. CONNECTIVITY (Using exact v2 DIDs)
            # ---------------------------------------------------------
            dids = self.phone_numbers_map.get(cid, [])

            if cpath == "Primary Flow":
                self.add_case(f"{prefix}1. Connectivity", "Internal Routing", f"Dial extension {cext} internally.", f"Call connects successfully to {cname} without dead air.")
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}1. Connectivity", f"External Routing (DID)", f"Dial the assigned DID {did} from a mobile phone.", f"Call connects via the PSTN to {cname} with high-quality, two-way audio.")
                else:
                    self.add_case(f"{prefix}1. Connectivity", "External Routing (No DID)", f"Dial the Main Company Number and enter extension {cext}.", f"Call successfully routes to {cname}.")

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
            elif not isinstance(bh_resp, dict) or not bh_resp:
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

            self.add_case(f"{prefix}2. Schedule Boundaries", "Business Hours (In-Hours)", f"{path_str}Initiate a call during Open Hours: [{bh_str}].", f"Call follows standard Business Hours routing path.")

            # ---------------------------------------------------------
            # 3. DETAILED ANSWERING RULES (Custom, After Hours)
            # ---------------------------------------------------------
            ar_summary_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules_summary = safe_list(ar_summary_resp, 'records') if isinstance(ar_summary_resp, dict) else []
            
            has_after_hours = False
            queue_settings = {}
            has_intro = False

            for rule_sum in rules_summary:
                if not isinstance(rule_sum, dict) or not rule_sum.get('enabled', False): continue
                rule_id = rule_sum.get('id')
                rtype = rule_sum.get('type')

                # Explicitly fetch the deep payload for accurate triggers/destinations
                rule = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule/{rule_id}', method='GET', raise_error=False)
                if not isinstance(rule, dict): rule = rule_sum

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    cond_str = self._extract_rule_conditions(rule)
                    action = rule.get('callHandlingAction')
                    tname, tid = self._resolve_target(rule, action)
                    
                    self.add_case(f"{prefix}2. Schedule Boundaries", f"Custom Rule: {rname}", f"{path_str}Initiate a call matching conditions: {cond_str}.", f"Rule successfully intercepts the call and routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}'"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    has_after_hours = True
                    action = rule.get('callHandlingAction')
                    tname, tid = self._resolve_target(rule, action)
                    
                    self.add_case(f"{prefix}2. Schedule Boundaries", "After Hours", f"{path_str}Initiate a call OUTSIDE of configured Business Hours.", f"Call executes After Hours logic and routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours Routing"})

                # --- QUEUE SETTINGS ---
                elif rtype == 'BusinessHours' and ctype == 'Department':
                    queue_settings = safe_dict(rule, 'queue')
                    greetings = safe_list(rule, 'greetings')
                    if any(isinstance(g, dict) and g.get('type') == 'Introductory' for g in greetings):
                        has_intro = True

            if not has_after_hours and bh_str != "24/7 (Always Open)":
                 self.add_case(f"{prefix}2. Schedule Boundaries", "After Hours", f"{path_str}Initiate a call OUTSIDE of configured Business Hours: [{bh_str}].", f"Follows default account After Hours logic.")

            # ---------------------------------------------------------
            # 4. CALL QUEUE EXHAUSTIVE TESTING
            # ---------------------------------------------------------
            if ctype == 'Department':
                q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/call-queue-info', method='GET', raise_error=False)
                if not isinstance(q_info, dict): q_info = {}
                
                tmode = q_info.get('transferMode') or queue_settings.get('transferMode') or 'Simultaneous'
                ag_timeout = q_info.get('agentTimeout') or queue_settings.get('agentTimeout') or 0
                wrap_up = q_info.get('wrapUpTime') or queue_settings.get('wrapUpTime') or 0
                hold_time = queue_settings.get('holdTime') or q_info.get('holdTime') or 0
                max_callers = queue_settings.get('maxCallers') or q_info.get('maxCallers') or 0
                int_per = q_info.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod') or 0

                # --- 3. Caller Experience ---
                if has_intro:
                    self.add_case(f"{prefix}3. Caller Experience", "Introductory Greeting", f"{path_str}Place a call to the queue.", "Configured Intro Greeting plays fully before agent ringing begins.")
                self.add_case(f"{prefix}3. Caller Experience", "Hold Music Verification", f"Remain in the queue while agents are busy/ringing.", "The configured connecting audio/hold music plays cleanly without distortion.")
                if int_per and int(int_per) > 0:
                    self.add_case(f"{prefix}3. Caller Experience", f"Interrupt Audio ({int_per}s)", f"Remain on hold in {cname} for at least {int(int_per) + 5} seconds.", f"At exactly {int_per}s, hold music pauses, the interrupt audio prompt plays, and hold music resumes.")

                # --- 4. Agent Experience ---
                self.add_case(f"{prefix}4. Agent Experience", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON in the RingEX App. Place a test call.", f"Agent's device rings. The Queue Name '{cname}' is prepended to the Caller ID.")
                self.add_case(f"{prefix}4. Agent Experience", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place a test call.", "Agent's device does NOT ring. The call seamlessly hunts to the next available agent.")
                self.add_case(f"{prefix}4. Agent Experience", "Active Call Decline", "While the queue call is ringing an agent, the agent actively clicks 'Decline'.", "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller.")
                if wrap_up and int(wrap_up) > 0:
                    self.add_case(f"{prefix}4. Agent Experience", f"Wrap-Up / ACW Timer ({wrap_up}s)", f"Agent answers a queue call and hangs up. Immediately place another call.", f"Agent enters 'Wrap-Up' status and does NOT receive the second call until the {wrap_up}s timer expires.")

                # --- 5. Routing & Distribution ---
                self.add_case(f"{prefix}5. Routing & Distribution", f"Distribution Method: {tmode}", f"Ensure multiple agents are 'Available'. Place a call into the queue.", f"The call distributes to agents based on {tmode} logic.")
                if str(tmode).lower() != 'simultaneous' and ag_timeout and int(ag_timeout) > 0:
                    self.add_case(f"{prefix}5. Routing & Distribution", f"Agent Ring Timeout ({ag_timeout}s)", f"Targeted agent lets the call ring without answering for exactly {ag_timeout} seconds.", f"The {ag_timeout}s timer expires. The call drops from Agent 1 and begins ringing the next available agent.")

                # --- 6. Call Handling ---
                self.add_case(f"{prefix}6. Call Handling", "Call Hold", "Agent answers the queue call and places the caller on hold using the RingEX App.", "The caller hears the agent hold music. The call can be successfully retrieved by the agent.")
                self.add_case(f"{prefix}6. Call Handling", "Warm Transfer", "Agent answers, initiates a Warm Transfer to an internal extension, consults, and completes.", "The caller is successfully connected to the secondary extension with two-way audio.")
                self.add_case(f"{prefix}6. Call Handling", "Blind Transfer", "Agent answers and initiates a Blind Transfer to an internal extension.", "The agent is immediately released. The caller is transferred and hears ringing to the secondary extension.")
                self.add_case(f"{prefix}6. Call Handling", "Call Park", "Agent answers and Parks the call to a Park Location.", "The caller is parked and hears hold music. The call can be successfully retrieved by another user dialing the park code.")

                # --- 7. Boundaries & Overflows ---
                h_act = queue_settings.get('holdTimeExpirationAction')
                h_name, h_id = self._resolve_target(queue_settings, h_act) 
                
                m_act = queue_settings.get('maxCallersAction')
                m_name, m_id = self._resolve_target(queue_settings, m_act)

                self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero Agents Logged In", f"Ensure ALL assigned agents are Logged Out or on DND. Initiate a call.", f"Call bypasses queue ringing and immediately executes overflow -> {h_name}.")

                if hold_time and int(hold_time) > 0:
                    h_mins = int(hold_time) // 60 if int(hold_time) >= 60 else int(hold_time)
                    lbl = f"{h_mins} minutes" if int(hold_time) >= 60 else f"{hold_time} seconds"
                    self.add_case(f"{prefix}7. Boundaries & Overflows", f"Max Wait Time Limit ({lbl})", f"Remain on hold in {cname} for exactly {lbl}.", f"Timer expires. Call is forcefully removed from the queue and routes to -> {h_name}.")
                    if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": f"Wait Time Overflow"})
                else:
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Unlimited Wait Time", f"Remain on hold in {cname} for over 10 minutes.", "No maximum wait time limit configured. Call remains in queue indefinitely.")

                if max_callers and int(max_callers) > 0:
                    self.add_case(f"{prefix}7. Boundaries & Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {cname} with {max_callers} calls. Dial call #{int(max_callers) + 1}.", f"The final call breaches the capacity limit. It is blocked and executes -> {m_name}.")
                    if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": f"Max Callers Overflow"})

                zero_name, _ = self._resolve_target(queue_settings, 'Voicemail')
                vmail = safe_dict(queue_settings, 'voicemail')
                if vmail or queue_settings.get('transfer'):
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero-Out (DTMF '0')", f"While listening to {cname} hold music, press '0' on the dialpad.", f"Call gracefully escapes the queue and routes to -> {zero_name}.")
                else:
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero-Out Disabled", f"While listening to {cname} hold music, press '0' on the dialpad.", "Input is safely ignored. Call remains in the queue.")

            # ---------------------------------------------------------
            # 5. IVR MENU TESTING
            # ---------------------------------------------------------
            elif ctype == 'IvrMenu':
                ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False)
                if not isinstance(ivr_info, dict): ivr_info = {}
                
                prompt = safe_dict(ivr_info, 'prompt')
                ptext = prompt.get('text', 'Configured Audio File')
                self.add_case(f"{prefix}3. Caller Experience", "Greeting Playback & Script", f"Dial {cname}.", f"The IVR prompt plays cleanly: '{ptext}'. Wording matches the officially approved script.")
                self.add_case(f"{prefix}3. Caller Experience", "Barge-In (Interruptibility)", f"While the greeting is actively playing, press a valid menu key.", "The IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the full message.")
                
                actions = safe_list(ivr_info, 'actions')
                if actions:
                    for act in actions:
                        if not isinstance(act, dict): continue
                        key = act.get('input', '')
                        if not key: continue
                        
                        a_type = act.get('action')
                        tname, tid = self._resolve_target(act, a_type)
                        
                        self.add_case(f"{prefix}5. Routing & Distribution", f"Key Mapping: Press '{key}'", f"{path_str}Listen to prompt and press '{key}'.", f"System correctly processes input and routes to -> {tname}.")
                        
                        if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Key '{key}'"})
                
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Dial-By-Extension Verification", f"While in the IVR, enter a known internal user's 3 or 4-digit extension.", "If general extension dialing is permitted, the IVR intercepts the string and transfers the call to the user.")
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Invalid Key Press", f"Press an unassigned key (e.g., '9' or '#').", "System plays an 'Invalid entry' error prompt and seamlessly replays the main menu.")
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Timeout (No Input)", f"Listen to the entire prompt and provide no DTMF input.", "System times out, replays the menu, and eventually executes the default timeout routing.")

            # ---------------------------------------------------------
            # 8. POST-CALL (Appended to each flow)
            # ---------------------------------------------------------
            self.add_case(f"{prefix}8. Post-Call", "Clean Disconnect", "During any active connected state, have the caller hang up.", "The call drops immediately across all endpoints. System returns agents to 'Available' status.")

        # ---------------------------------------------------------
        # GLOBAL ACCOUNT CHECKS
        # ---------------------------------------------------------
        self.add_case("Global Validation", "Voicemail Deposit & Delivery", "Trigger any tested routing scenario that routes to Voicemail. Leave a test message. Check the designated target's inbox.", "The correct Voicemail greeting plays. The voicemail audio file is recorded without truncating and delivered accurately.")
        self.add_case("Global Validation", "Call Logs Generation", "Log into the RingCentral Admin Portal and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct originating Caller ID, target extensions, duration, and final routing result across the entire traced journey.")


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Entry point for the UI router."""
    generator = UATGenerator(extension_id, extension_name, extension_number, extension_type)
    generator.process()
    return generator.test_cases
