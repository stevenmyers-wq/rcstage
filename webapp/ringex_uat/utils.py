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

    def resolve_site(ext):
        if ext.get('type') == 'Site':
            return ext.get('name', 'Main Site')
        site = safe_dict(ext, 'site')
        return site.get('name') or 'Main Site'

    entities = [
        {"id": ext.get('id'), "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext.get('type'), "site": resolve_site(ext)}
        for ext in records if isinstance(ext, dict) and ext.get('type') in valid_types
    ]
    return sorted(entities, key=lambda x: x['name'])


class UATGenerator:
    """Forensic, data-driven crawler that translates raw API JSON directly into holistic UAT cases."""
    
    def __init__(self, start_ext_id, start_ext_name, start_ext_number, start_ext_type, complexity='complex'):
        # 'complex' emits every case; 'standard' trims to happy-path routing plus ring timers and audio.
        self.complexity = complexity if complexity in ('standard', 'complex') else 'complex'
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

    def add_target(self, ext_id, ext_name, ext_number, ext_type):
        """Seeds an additional Primary Flow target into the crawl queue (multi-select support)."""
        self.queue_to_process.append({
            "id": str(ext_id),
            "name": ext_name,
            "ext": ext_number,
            "type": ext_type,
            "path": "Primary Flow"
        })

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

    def add_case(self, category, scenario, action, expected, level='standard'):
        """Appends a test case. Cases tagged level='complex' are skipped in Standard mode."""
        if level == 'complex' and self.complexity == 'standard':
            return
        self.test_cases.append({
            "test_id": f"UAT-{self.counter:03d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        self.counter += 1

    def _resolve_target(self, rule_obj, action_type=None, current_ext_name="Unknown"):
        """Forensically maps specific overflow/transfer actions and RC backend Enums to human destinations."""
        if not isinstance(rule_obj, dict):
            return f"Action: {action_type or 'Unknown'}", None
            
        act = action_type or rule_obj.get('callHandlingAction') or 'Unknown Action'
            
        if act in ['TransferToExtension', 'Bypass', 'Transfer']:
            if act == 'Bypass':
                tid = str(safe_dict(rule_obj, 'extension').get('id', ''))
            else:
                transfer = safe_dict(rule_obj, 'transfer')
                if not transfer: 
                    t_list = safe_list(rule_obj, 'transfer')
                    if t_list and isinstance(t_list[0], dict): transfer = t_list[0]
                    
                tid = str(safe_dict(transfer, 'extension').get('id', ''))
                
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}") if tid else "Unknown Extension"
            text = self.ext_map.get(tid, {}).get('ext', '')
            ext_str = f" (Ext {text})" if text else ""
            return f"Internal Extension: {tname}{ext_str}", tid
            
        elif act in ['TakeMessagesReturnToGreeting', 'TakeMessagesOnly', 'Voicemail']:
            voicemail = safe_dict(rule_obj, 'voicemail')
            
            # Check if voicemail is actually enabled. If false, it falls back to missedCall behavior.
            if voicemail.get('enabled', True) is False:
                missed = safe_dict(rule_obj, 'missedCall')
                m_act = missed.get('actionType', 'PlayGreetingAndDisconnect')
                
                if m_act == 'ConnectToExtension':
                    tid = str(safe_dict(missed, 'extension').get('id', ''))
                    tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}") if tid else "Unknown"
                    text = self.ext_map.get(tid, {}).get('ext', '')
                    ext_str = f" (Ext {text})" if text else ""
                    return f"Missed Call Transfer -> {tname}{ext_str}", tid
                elif m_act == 'ConnectToExternalNumber':
                    num = missed.get('phoneNumber', 'Unknown Number')
                    return f"Missed Call Forward -> {num}", None
                else:
                    return "Play Missed Call Greeting & Disconnect", None
            else:
                # Standard Voicemail Routing
                recip = safe_dict(voicemail, 'recipient')
                tid = str(recip.get('id', ''))
                if tid:
                    tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}")
                    text = self.ext_map.get(tid, {}).get('ext', '')
                    ext_str = f" (Ext {text})" if text else ""
                    return f"Voicemail Inbox of {tname}{ext_str}", tid
                else:
                    return f"Voicemail Inbox of {current_ext_name}", None
            
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
            return "Wait for Primary Members to Answer", None
            
        elif act == 'WaitPrimaryAndOverflowMembers':
            return "Wait for Primary & Overflow Members to Answer", None
            
        elif act == 'AgentQueue':
            return "Queue Agents (Standard Queue Distribution)", None
            
        elif act == 'ForwardCalls':
            fwd_obj = safe_dict(rule_obj, 'forwarding')
            fwd_rules = safe_list(fwd_obj, 'rules')
            destinations = []
            
            for fr in fwd_rules:
                if isinstance(fr, dict):
                    nums = safe_list(fr, 'forwardingNumbers')
                    for fn in nums:
                        if isinstance(fn, dict):
                            label = fn.get('label') or fn.get('phoneNumber')
                            if label: destinations.append(label)
                            
            if destinations:
                return f"Forwarding Array [{', '.join(destinations)}]", None
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
            sched_strs = []
            
            # Extract specific days and times
            weekly = safe_dict(sched, 'weeklyRanges')
            if weekly:
                days = []
                for d, t_list in weekly.items():
                    if isinstance(t_list, list) and len(t_list) > 0 and isinstance(t_list[0], dict):
                        days.append(f"{d[:3]} {t_list[0].get('from')}-{t_list[0].get('to')}")
                if days:
                    sched_strs.append(f"Days: [{', '.join(days)}]")
            
            # Extract specific dates (like holidays)
            ranges = safe_list(sched, 'ranges')
            if ranges:
                dates = []
                for r in ranges:
                    if isinstance(r, dict) and r.get('from') and r.get('to'):
                        dates.append(f"{r.get('from')} to {r.get('to')}")
                if dates:
                    sched_strs.append(f"Dates: [{', '.join(dates)}]")
            
            if sched_strs:
                conditions.append(" AND ".join(sched_strs))
            else:
                conditions.append("Matches Custom Time Schedule")
            
        return " AND ".join(conditions) if conditions else "Specific Configured Condition"

    def _get_greeting_info(self, rule_obj, greeting_type):
        """Determines if a specific greeting type is active and extracts if it is Custom or Preset."""
        greetings = safe_list(rule_obj, 'greetings')
        for g in greetings:
            if isinstance(g, dict) and g.get('type') == greeting_type:
                # If there's a custom uploaded greeting, it's active
                if 'custom' in g and g['custom']:
                    return {'active': True, 'desc': 'Custom'}
                # If there's a preset, ensure it's not the "None" off switch
                if 'preset' in g:
                    preset_name = safe_dict(g, 'preset').get('name', '')
                    if preset_name not in ['None', 'Off']:
                        return {'active': True, 'desc': f"Preset: {preset_name}"}
                    else:
                        return {'active': False, 'desc': 'None'}
        return {'active': False, 'desc': 'System Default'}

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
                self.add_case(f"{prefix}1. Connectivity", "Internal Routing", f"Dial extension {cext} internally.", f"Call connects successfully to {cname}.")
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}1. Connectivity", f"External Routing (DID)", f"Dial the assigned DID {did} from a non RingCentral phone.", f"Call connects via the PSTN to {cname}.")
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

            # ---------------------------------------------------------
            # 3. DETAILED ANSWERING RULES (Custom, After Hours)
            # ---------------------------------------------------------
            ar_summary_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules_summary = safe_list(ar_summary_resp, 'records') if isinstance(ar_summary_resp, dict) else []
            
            has_after_hours = False
            has_business_hours = False
            queue_settings = {}
            intro_info = {'active': False, 'desc': 'System Default'}
            conn_audio_info = {'active': True, 'desc': 'System Default'}
            hold_music_info = {'active': True, 'desc': 'System Default'}

            for rule_sum in rules_summary:
                if not isinstance(rule_sum, dict) or not rule_sum.get('enabled', False): continue
                rule_id = rule_sum.get('id')
                rtype = rule_sum.get('type')

                # Explicitly fetch the deep payload for accurate triggers/destinations
                rule = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule/{rule_id}', method='GET', raise_error=False)
                if not isinstance(rule, dict): rule = rule_sum

                # --- CALL SCREENING CHECK ---
                screening = rule.get('screening')
                if screening and screening != 'Off':
                    self.add_case(f"{prefix}3. Caller Experience", f"Call Screening ({screening})", f"{path_str}Initiate a call triggering the {rtype} rule.", "Caller is prompted to record their name before the call is connected.")

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    cond_str = self._extract_rule_conditions(rule)
                    action = rule.get('callHandlingAction')
                    tname, tid = self._resolve_target(rule, action, cname)
                    
                    self.add_case(f"{prefix}2. Schedule Boundaries", f"Custom Rule: {rname}", f"{path_str}Initiate a call matching conditions: {cond_str}.", f"Rule successfully intercepts the call and routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}'"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    has_after_hours = True
                    action = rule.get('callHandlingAction')
                    tname, tid = self._resolve_target(rule, action, cname)
                    
                    self.add_case(f"{prefix}2. Schedule Boundaries", "After Hours", f"{path_str}Initiate a call OUTSIDE of Business Hours: [{bh_str}].", f"Call executes After Hours logic and routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours Routing"})

                # --- BUSINESS HOURS / QUEUE SETTINGS ---
                elif rtype == 'BusinessHours':
                    has_business_hours = True
                    action = rule.get('callHandlingAction', 'AgentQueue' if ctype == 'Department' else 'Unknown')
                    tname, tid = self._resolve_target(rule, action, cname)
                    
                    self.add_case(f"{prefix}2. Schedule Boundaries", "Business Hours", f"{path_str}Initiate a call during Open Hours: [{bh_str}].", f"Call follows Business Hours routing path and routes to -> {tname}.")
                    
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Business Hours Routing"})
                    
                    if ctype == 'Department':
                        queue_settings = safe_dict(rule, 'queue')
                        intro_info = self._get_greeting_info(rule, 'Introductory')
                        
                        c_info = self._get_greeting_info(rule, 'ConnectingAudio')
                        if c_info['desc'] != 'System Default': conn_audio_info = c_info
                        
                        h_info = self._get_greeting_info(rule, 'HoldMusic')
                        if h_info['desc'] != 'System Default': hold_music_info = h_info

            if not has_business_hours:
                self.add_case(f"{prefix}2. Schedule Boundaries", "Business Hours", f"{path_str}Initiate a call during Open Hours: [{bh_str}].", f"Call follows Business Hours routing path and routes to -> System Default.")

            if not has_after_hours and bh_str != "24/7 (Always Open)":
                 self.add_case(f"{prefix}2. Schedule Boundaries", "After Hours", f"{path_str}Initiate a call OUTSIDE of Business Hours: [{bh_str}].", f"Follows default account After Hours logic.")

            if cpath == "Primary Flow":
                self.add_case(f"{prefix}2. Schedule Boundaries", "Holiday / Special Schedule Override", f"On a configured holiday or special-hours date, place a call that would normally hit Business Hours: [{bh_str}].", "The holiday / special schedule takes precedence over the normal Business Hours and After Hours logic, and the call routes to the holiday destination.", level='complex')

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
                
                int_mode = q_info.get('holdAudioInterruptionMode') or queue_settings.get('holdAudioInterruptionMode') or 'Never'
                int_per = q_info.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod') or 0

                # --- 3. Caller Experience ---
                if intro_info['active']:
                    self.add_case(f"{prefix}3. Caller Experience", "Introductory Greeting", f"{path_str}Place a call to the queue.", f"The [{intro_info['desc']}] Introductory Greeting plays fully before agent ringing begins.")
                self.add_case(f"{prefix}3. Caller Experience", "Audio While Connecting", f"Remain in the queue while waiting for an agent.", f"The [{conn_audio_info['desc']}] connecting audio plays cleanly without distortion.")
                
                if int_mode == 'Periodically' and int_per and int(int_per) > 0:
                    self.add_case(f"{prefix}3. Caller Experience", f"Interrupt Audio (Every {int_per}s)", f"Remain on hold in {cname} for at least {int(int_per) + 5} seconds.", f"At exactly {int_per}s, audio pauses, the interrupt prompt plays, and connecting audio resumes.")
                elif int_mode == 'WhenMusicEnds':
                    self.add_case(f"{prefix}3. Caller Experience", "Interrupt Audio (When Music Ends)", f"Remain on hold in {cname} until the connecting audio track finishes.", "When the audio track ends, the interrupt prompt plays before looping.")

                # --- 4. Agent Experience ---
                self.add_case(f"{prefix}4. Agent Experience", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON in the RingEX App. Place a test call.", f"Agent's device rings. The Queue Name '{cname}' is prepended to the Caller ID.", level='complex')
                
                if str(tmode).lower() == 'simultaneous':
                    opt_out_exp = "Agent's device does NOT ring. The call rings other available members."
                    decline_exp = "Ringing stops for that agent immediately. The call rings other available members without dropping the caller."
                else:
                    opt_out_exp = "Agent's device does NOT ring. The call seamlessly hunts to the next available agent."
                    decline_exp = "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller."
                
                self.add_case(f"{prefix}4. Agent Experience", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place a test call.", opt_out_exp, level='complex')
                self.add_case(f"{prefix}4. Agent Experience", "Active Call Decline", f"While the queue call is ringing an agent, the agent actively clicks 'Decline'.", decline_exp, level='complex')

                if wrap_up and int(wrap_up) > 0:
                    self.add_case(f"{prefix}4. Agent Experience", f"Wrap-Up / ACW Timer ({wrap_up}s)", f"Agent answers a queue call and hangs up. Immediately place another call.", f"Agent enters 'Wrap-Up' status and does NOT receive the second call until the {wrap_up}s timer expires.", level='complex')

                # --- 5. Routing & Distribution ---
                # Check for explicit fixed order agent array
                if str(tmode).lower() == 'fixedorder':
                    agents_arr = safe_list(queue_settings, 'fixedOrderAgents') or safe_list(q_info, 'fixedOrderAgents')
                    if agents_arr:
                        agent_names = []
                        for a in agents_arr:
                            if isinstance(a, dict) and safe_dict(a, 'extension').get('id'):
                                a_id = str(safe_dict(a, 'extension').get('id'))
                                agent_names.append(self.ext_map.get(a_id, {}).get('name', f'Ext {a_id}'))
                        agent_str = " -> ".join(agent_names) if agent_names else "Configured Agents"
                        self.add_case(f"{prefix}5. Routing & Distribution", "Fixed Order Distribution", f"Ensure agents are 'Available'. Place a call.", f"Call rings agents sequentially in exactly this order: [{agent_str}].")
                else:
                    self.add_case(f"{prefix}5. Routing & Distribution", f"Distribution Method: {tmode}", f"Ensure multiple agents are 'Available'. Place a call into the queue.", f"The call distributes to agents based on {tmode} logic.")
                
                if str(tmode).lower() != 'simultaneous' and ag_timeout and int(ag_timeout) > 0:
                    self.add_case(f"{prefix}5. Routing & Distribution", f"Agent Ring Timeout ({ag_timeout}s)", f"Targeted agent lets the call ring without answering for exactly {ag_timeout} seconds.", f"The {ag_timeout}s timer expires. The call drops from Agent 1 and begins ringing the next available agent.")

                # --- 6. Call Handling ---
                self.add_case(f"{prefix}6. Call Handling", "Call Hold (Hold Music)", "Agent answers the queue call and places the caller on hold using the RingEX App.", f"The caller hears the [{hold_music_info['desc']}] hold music. The call can be successfully retrieved by the agent.", level='complex')
                self.add_case(f"{prefix}6. Call Handling", "Warm Transfer", "Agent answers, initiates a Warm Transfer to an internal extension, consults, and completes.", "The caller is successfully connected to the secondary extension with two-way audio.", level='complex')
                self.add_case(f"{prefix}6. Call Handling", "Blind Transfer", "Agent answers and initiates a Blind Transfer to an internal extension.", "The agent is immediately released. The caller is transferred and hears ringing to the secondary extension.", level='complex')
                self.add_case(f"{prefix}6. Call Handling", "Call Park", "Agent answers and Parks the call to a Park Location.", f"The caller is parked and hears the [{hold_music_info['desc']}] hold music. The call can be retrieved by dialing the park code.", level='complex')

                # --- 7. Boundaries & Overflows ---
                no_ans_act = queue_settings.get('noAnswerAction')
                if no_ans_act:
                    if no_ans_act in ['WaitPrimaryMembers', 'WaitPrimaryAndOverflowMembers']:
                        self.add_case(f"{prefix}7. Boundaries & Overflows", f"No Answer Action ({no_ans_act})", f"Ensure agents are available but let the call ring without being answered.", f"Agents stop ringing. Call remains in queue to continue waiting/hunting.", level='complex')
                    else:
                        na_name, na_id = self._resolve_target(queue_settings, no_ans_act, cname)
                        self.add_case(f"{prefix}7. Boundaries & Overflows", f"No Answer Action ({no_ans_act})", f"Ensure agents are available but let the call ring without being answered.", f"Agents stop ringing. Call executes overflow -> {na_name}.", level='complex')
                        if na_id and self.ext_map.get(na_id, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                            self.queue_to_process.append({"id": na_id, "name": self.ext_map[na_id]['name'], "ext": self.ext_map[na_id]['ext'], "type": self.ext_map[na_id]['type'], "path": f"No Answer Overflow"})

                h_act = queue_settings.get('holdTimeExpirationAction')
                h_name, h_id = self._resolve_target(queue_settings, h_act, cname)

                m_act = queue_settings.get('maxCallersAction')
                m_name, m_id = self._resolve_target(queue_settings, m_act, cname)

                self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero Agents Logged In", f"Ensure ALL assigned agents are Logged Out or on DND. Initiate a call.", f"Call bypasses queue ringing and immediately executes overflow -> {h_name}.", level='complex')

                if hold_time and int(hold_time) > 0:
                    h_mins = int(hold_time) // 60 if int(hold_time) >= 60 else int(hold_time)
                    lbl = f"{h_mins} minutes" if int(hold_time) >= 60 else f"{hold_time} seconds"
                    self.add_case(f"{prefix}7. Boundaries & Overflows", f"Max Wait Time Limit ({lbl})", f"Remain on hold in {cname} for exactly {lbl}.", f"Timer expires (Note: time calculated after any introductory audio finishes). Call is forcefully removed from the queue and routes to -> {h_name}.")
                    if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                        self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": f"Wait Time Overflow"})
                else:
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Unlimited Wait Time", f"Remain on hold in {cname} for over 10 minutes.", "No maximum wait time limit configured. Call remains in queue indefinitely.")

                if max_callers and int(max_callers) > 0:
                    call_target = dids[0] if dids else f"extension {cext}"
                    self.add_case(f"{prefix}7. Boundaries & Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously call {call_target} with {max_callers} calls. Dial call #{int(max_callers) + 1}.", f"The final call breaches the capacity limit. It is forwarded and executes -> {m_name}.", level='complex')
                    if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                        self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": f"Max Callers Overflow"})

                vmail = safe_dict(queue_settings, 'voicemail')
                if vmail or queue_settings.get('transfer'):
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero-Out (DTMF '0')", f"While listening to {cname} hold music, press '0' on the dialpad.", f"Call gracefully escapes the queue and routes to the applicable '0' destination (e.g. Voicemail or Operator).", level='complex')
                else:
                    self.add_case(f"{prefix}7. Boundaries & Overflows", "Zero-Out Disabled", f"While listening to {cname} hold music, press '0' on the dialpad.", "Input is safely ignored. Call remains in the queue.", level='complex')

                self.add_case(f"{prefix}7. Boundaries & Overflows", "Caller Abandons in Queue", f"While waiting on hold in {cname} (before any agent answers), the caller hangs up.", "The caller is removed from the queue immediately, agents stop being presented with that call, and no agent is left ringing a dead call. The vacated position is freed for the next caller.", level='complex')

            # ---------------------------------------------------------
            # 5. IVR MENU TESTING
            # ---------------------------------------------------------
            elif ctype == 'IvrMenu':
                ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False)
                if not isinstance(ivr_info, dict): ivr_info = {}
                
                prompt = safe_dict(ivr_info, 'prompt')
                mode = prompt.get('mode', '')
                if mode == 'Audio':
                    ptext = "[Custom Audio]"
                elif mode == 'TextToSpeech':
                    ptext = f"[Text-to-Speech] '{prompt.get('text', '')}'"
                else:
                    ptext = f"[Preset/Audio] {prompt.get('text', 'Configured Audio File')}"
                    
                self.add_case(f"{prefix}3. Caller Experience", "Greeting Playback & Script", f"Dial {cname}.", f"The IVR prompt {ptext} plays cleanly. Wording matches the officially approved script.")
                self.add_case(f"{prefix}3. Caller Experience", "Barge-In (Interruptibility)", f"While the greeting is actively playing, press a valid menu key.", "The IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the full message.", level='complex')
                self.add_case(f"{prefix}3. Caller Experience", "DTMF Reliability (Cross-Network)", f"Dial {cname} from a mobile, a PSTN landline, and a softphone. On each, press the same valid menu key.", "The DTMF tone is registered reliably on every originating network and routes to the identical destination each time, with no dropped or double digits.", level='complex')

                actions = safe_list(ivr_info, 'actions')
                if actions:
                    for act in actions:
                        if not isinstance(act, dict): continue
                        key = act.get('input', '')
                        if not key: continue
                        
                        a_type = act.get('action')
                        tname, tid = self._resolve_target(act, a_type, cname)
                        
                        self.add_case(f"{prefix}5. Routing & Distribution", f"Key Mapping: Press '{key}'", f"{path_str}Listen to prompt and press '{key}'.", f"System correctly processes input and routes to -> {tname}.")
                        
                        if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu', 'Site']:
                            self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Key '{key}'"})
                
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Dial-By-Extension Verification", f"While in the IVR, enter a known internal user's 3 or 4-digit extension.", "If general extension dialing is permitted, the IVR intercepts the string and transfers the call to the user.", level='complex')
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Invalid Key Press", f"Press an unassigned key (e.g., '9' or '#').", "System plays an 'Invalid entry' error prompt and seamlessly replays the main menu.", level='complex')
                self.add_case(f"{prefix}7. Boundaries & Overflows", "Timeout (No Input)", f"Listen to the entire prompt and provide no DTMF input.", "System times out, replays the menu, and eventually executes the default timeout routing.", level='complex')

        # ---------------------------------------------------------
        # GLOBAL ACCOUNT CHECKS
        # ---------------------------------------------------------
        self.add_case("Global Validation", "Voicemail Deposit & Delivery", "Trigger any tested routing scenario that routes to Voicemail. Leave a test message. Check the designated target's inbox.", "The configured Voicemail greeting plays. The voicemail audio file is recorded without truncating and delivered accurately.")
        self.add_case("Global Validation", "Voicemail Full / Disabled Fallback", "Route a call to a mailbox that is full (or has voicemail disabled). Let the call reach the voicemail step.", "The system does not error or drop silently. It follows the configured fallback (e.g. plays a 'mailbox full' greeting and disconnects, or executes the missed-call overflow).", level='complex')
        self.add_case("Global Validation", "Call Logs Generation", "Log into the RingCentral Admin Portal (Go to: https://service.ringcentral.com) and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct originating Caller ID, target extensions, duration, and final routing result across the entire traced journey.")


# =============================================================================
# STANDALONE SUITE CHECKLISTS (tenant-level, independent of a routing target)
# =============================================================================

def _suite(cat, url, rows):
    """Builds suite case dicts. When ``url`` is set, a 'go to' portal link is
    appended to each action step; integration/config suites pass no URL so that
    no setup link is shown."""
    def build_action(a):
        return f"{a} (Go to: {url})" if url else a
    return [
        {"category": cat, "scenario": s, "action": build_action(a), "expected": e}
        for (s, a, e) in rows
    ]


def _user_test_cases():
    return _suite("User Tests", "https://app.ringcentral.com", [
        ("Inbound to Direct Number & Extension", "Dial the user's direct DID and, separately, their extension from another phone.", "Both calls ring the user's app and all provisioned devices, presenting the correct caller ID."),
        ("Outbound Call", "Place an outbound external call from the user's app or device.", "Call connects with two-way audio and presents the correct outbound caller ID / company number."),
        ("Voicemail Deposit & Retrieval", "Leave the user a voicemail, then retrieve it from the app and the message-waiting indicator.", "The personal greeting plays, the message is recorded and delivered, and the MWI clears once played."),
        ("Call Forwarding", "Enable call forwarding to another number, then place an inbound call to the user.", "The inbound call follows the forwarding rule to the configured destination."),
        ("Do Not Disturb / Presence", "Set the user to DND, place a test call, then check presence on a colleague's BLF/monitored key.", "Inbound calls follow the busy/voicemail path and the user's presence shows as DND/busy to others."),
        ("Simultaneous Ring (Multi-Device)", "With multiple devices provisioned (app, desk phone), place a call to the user.", "All devices ring together; answering on one immediately stops the others."),
        ("Warm & Blind Transfer", "As the user, answer a call and perform a warm transfer, then repeat with a blind transfer.", "Warm transfer connects after consult; blind transfer releases the user immediately and connects the caller."),
        ("Caller ID Name (CNAM)", "Place inbound and outbound calls and inspect the displayed name on both ends.", "The correct caller ID name is presented for internal and external calls."),
    ])


def _device_test_cases():
    return _suite("Device Tests (Hardphone)", "https://service.ringcentral.com", [
        ("Provisioning & Registration", "Factory-reset or reboot the hardphone and allow it to provision.", "The phone provisions automatically, registers (SIP registered), and populates the correct line keys and extension label."),
        ("Inbound Call & Display", "Place an inbound call to the phone's extension/DID.", "The phone rings, shows the correct caller ID on the display, and can be answered via handset, speaker, and headset."),
        ("Outbound Call & Audio", "Dial an external number from the keypad.", "The call connects with clear two-way audio and no echo or one-way audio on handset, speaker, and headset."),
        ("Hold / Resume (Hard Key)", "Answer a call and use the phone's Hold key, then resume.", "The caller is held (hears hold music) and is cleanly resumed with two-way audio."),
        ("Attended & Blind Transfer (Hard Keys)", "Use the phone's Transfer key for an attended transfer, then a blind transfer.", "Both transfer types complete correctly using the device keys."),
        ("BLF / Presence Keys", "Observe a monitored extension's BLF key while that user is idle, ringing, and on a call.", "The BLF key reflects the correct status and press-to-dial / call-pickup works."),
        ("Paging / Intercom", "Initiate a page or intercom call to the device (if configured).", "The device auto-answers the page/intercom and audio is delivered as configured."),
        ("Firmware Baseline", "Check the phone's firmware version in the admin portal or device menu.", "Firmware matches the approved/expected baseline for the model."),
        ("Power & Network Resilience", "Remove PoE/power or network from the phone, then restore it.", "On restore, the phone re-registers automatically within the expected window without manual intervention."),
        ("Emergency (E911) Address", "Verify the emergency address assigned to the device, then place a test call to the designated E911 test number.", "The correct registered emergency location is associated and the test call routes as expected."),
    ])


def _scim_test_cases():
    return _suite("SCIM Provisioning", None, [
        ("User Provisioning (Create)", "Create a new user in the identity provider and assign the RingEX app.", "The user is auto-provisioned in RingEX with the correct attributes, extension, and license within the expected sync window."),
        ("Attribute Update (Sync)", "Change a user's name, department, or contact attribute in the IdP.", "The updated attributes sync through to the RingEX user record."),
        ("Group / Role Mapping", "Add or remove the user from an IdP group mapped to a RingEX role or template (if configured).", "The user receives the correct role / template based on group membership."),
        ("Deprovisioning (Disable)", "Disable or unassign the user in the IdP.", "The RingEX user is disabled/removed and the license is reclaimed per policy."),
        ("Re-activation", "Re-enable a previously disabled user in the IdP.", "The user is restored in RingEX with the expected configuration."),
        ("Error Handling", "Attempt to provision an invalid or duplicate user (e.g. duplicate extension/email).", "SCIM returns a clear error and does not create a partial or conflicting record."),
    ])


def _teams_direct_routing_test_cases():
    return _suite("MS Teams Direct Routing", None, [
        ("Inbound PSTN to Teams User", "Call the DID of a Teams-enabled (Direct Routing) user from an external phone.", "The call rings inside the Microsoft Teams client and connects with two-way audio."),
        ("Outbound Teams to PSTN", "From the Teams client, dial an external PSTN number.", "The call routes out via the RingCentral SBC and presents the correct outbound caller ID."),
        ("Teams to RingEX Internal", "Place a call between a Teams Direct Routing user and a native RingEX extension.", "Internal call connects both directions with correct caller ID."),
        ("Unanswered Call to Voicemail", "Leave an inbound call to a Teams user unanswered.", "The call follows the configured voicemail path (Teams or RingEX) as designed."),
        ("Transfer to Queue / IVR", "From a Teams call, transfer the caller to a RingEX call queue or IVR.", "The transfer completes and the caller enters the queue/IVR correctly."),
        ("Emergency Calling", "Place an emergency (or E911 test) call from the Teams client.", "The call routes correctly with the appropriate location information."),
    ])


def _teams_embedded_app_test_cases():
    return _suite("MS Teams Embedded App", None, [
        ("App Install & Load", "Install/pin the RingCentral embedded app in Microsoft Teams and open it.", "The app appears in Teams and loads without error."),
        ("Single Sign-On to App", "Open the embedded app as a provisioned user (SSO configured).", "The user is authenticated into the embedded app without a separate manual login."),
        ("Click-to-Dial", "Initiate a call from the embedded dialer or a Teams contact.", "The call is placed through RingEX and connects with audio."),
        ("Inbound Call Notification", "Place an inbound call to the user while the embedded app is running.", "The incoming call surfaces in the Teams embedded app and can be answered from it."),
        ("Messaging / SMS / Fax Access", "Access messaging, SMS, or fax features within the embedded app.", "The features load and function within Teams for the entitled user."),
    ])


def _teams_presence_test_cases():
    return _suite("MS Teams Presence Sync (Teams <> RingEX)", None, [
        ("Teams Call -> RingEX Busy", "Place the user on an active Microsoft Teams call.", "The user's RingEX presence updates to 'On a call' / busy for the duration of the Teams call."),
        ("RingEX Call -> Teams Busy", "Place the user on an active RingEX call.", "The user's Microsoft Teams presence updates to 'In a call' / busy for the duration of the RingEX call."),
        ("Teams Status -> RingEX", "Manually change the user's Teams status (Available, Away, Busy, Do Not Disturb).", "The equivalent RingEX presence reflects the Teams status within the expected sync window."),
        ("RingEX Status -> Teams", "Manually change the user's RingEX presence (Available, Do Not Disturb, Invisible).", "The equivalent Microsoft Teams status reflects the RingEX presence within the expected sync window."),
        ("Do Not Disturb Suppression", "Set the user to Do Not Disturb in Teams, then place a test call.", "DND is honoured across the integration and RingEX call notifications are suppressed as configured."),
        ("Presence Clears on Hang-Up", "End both a Teams call and a RingEX call.", "Presence returns to the prior/available state on both platforms once each call ends, with no stuck 'busy' status."),
    ])


def _sso_test_cases():
    return _suite("Single Sign-On (SSO)", "https://service.ringcentral.com", [
        ("SP-Initiated Login", "Start at the RingEX login page and choose SSO.", "The user is redirected to the IdP, authenticates, and lands signed in to RingEX."),
        ("Native App Login", "Sign in to the RingEX desktop and mobile apps via SSO.", "The SSO flow completes in the native apps and the session is established."),
        ("Invalid / Denied Credentials", "Attempt SSO with invalid credentials or an unauthorized account.", "Access is denied gracefully with a clear message and no session is created."),
        ("Deprovisioned User", "Disable the user in the IdP, then attempt SSO.", "SSO is denied and the user cannot access RingEX."),
        ("Logout & MFA", "Complete a logout, then verify MFA is enforced on the next login (if configured).", "Logout terminates the session and MFA is enforced as configured."),
    ])


def _crm_test_cases():
    # Out-of-the-box RingCentral CRM integrations (native managed packages + App Connect).
    return _suite("CRM Integration (App Connect / Native)", None, [
        ("Supported CRM Confirmation", "Confirm the customer's CRM is an out-of-the-box RingCentral integration (native: Salesforce, HubSpot, Microsoft Dynamics 365, ServiceNow, Zendesk, Zoho; App Connect: Pipedrive, Insightly, Bullhorn, NetSuite, Clio, Redtail) and that the integration/extension is installed.", "The CRM integration is installed and authorised, and the RingCentral dialer/App Connect widget loads inside the CRM."),
        ("Login / Authorisation", "Log in to RingCentral from within the CRM integration.", "Authentication completes and the embedded phone connects and shows a ready/registered state."),
        ("Click-to-Dial", "Click a phone number on a CRM contact/lead record.", "The call is initiated through RingCentral and connects with two-way audio."),
        ("Inbound Screen Pop / Contact Match", "Place an inbound call from a number that exists on a CRM record.", "The matching CRM record pops/surfaces for the agent as the call arrives."),
        ("Unknown Caller Handling", "Place an inbound call from a number not in the CRM.", "The integration offers to create a new record (or follows the configured unknown-caller behaviour) without error."),
        ("Automatic Call Logging", "Complete inbound and outbound calls, then check the CRM record's activity history.", "A call activity is logged automatically against the correct record with accurate direction, timestamp, and duration."),
        ("Call Notes & Disposition", "During or after a call, add notes and set a disposition/outcome in the widget.", "Notes and disposition save to the associated CRM activity record."),
        ("Recording / Transcript Link", "For a recorded call, open the logged CRM activity.", "The call recording (and transcript, where ACE/RingSense is enabled) is attached or linked on the CRM activity as configured."),
    ])


def _ace_test_cases():
    # ACE = AI Conversation Expert (formerly RingSense): recording, transcription, AI insights.
    return _suite("ACE / RingSense (AI Conversation Expert)", None, [
        ("Recording Capture", "Place a call on an ACE/RingSense-enabled extension or queue with recording enabled.", "The call is recorded and appears in the ACE/RingSense library for processing."),
        ("Transcription Accuracy", "Open the processed call and review the transcript.", "A speaker-separated transcript is generated and reasonably reflects the spoken conversation."),
        ("AI Call Summary", "Open the AI-generated summary for the processed call.", "A concise, accurate summary of the conversation is produced."),
        ("Action Items & Next Steps", "Review the extracted action items / next steps on the call.", "Relevant action items and follow-ups are surfaced from the conversation."),
        ("Sentiment & Topic Detection", "Review the sentiment score and detected keywords/topics/trackers.", "Sentiment and topics/trackers are analysed and displayed for the call."),
        ("Ask ACE Query", "Use the 'Ask ACE' conversational interface to ask a question about tested calls.", "ACE returns a relevant, accurate natural-language answer drawn from the analysed conversations."),
        ("Access & Permissions", "Log in as a user without ACE/RingSense entitlement and attempt to view AI insights.", "AI insights are only visible to entitled/permitted users per the configured access policy."),
    ])


SUITE_BUILDERS = {
    "user": _user_test_cases,
    "device": _device_test_cases,
    "scim": _scim_test_cases,
    "teams_direct_routing": _teams_direct_routing_test_cases,
    "teams_embedded": _teams_embedded_app_test_cases,
    "teams_presence": _teams_presence_test_cases,
    "crm": _crm_test_cases,
    "ace": _ace_test_cases,
    "sso": _sso_test_cases,
}


def generate_uat_cases(targets=None, complexity='complex', suites=None):
    """Entry point for the UI router.

    Crawls one or more routing targets (multi-select), then appends any selected
    standalone suite checklists (User, Device, SCIM, Teams, SSO). All selected
    targets are seeded as Primary Flows into a single crawler so shared downstream
    flows are de-duplicated across them. Test IDs are assigned sequentially across
    the whole combined script.

    ``targets`` is a list of dicts: {"id", "name", "number", "type"}.
    """
    targets = targets or []
    suites = suites or []
    cases = []

    valid_targets = [t for t in targets if isinstance(t, dict) and t.get('id')]
    if valid_targets:
        first = valid_targets[0]
        generator = UATGenerator(first.get('id'), first.get('name', 'Unknown'),
                                 first.get('number', 'N/A'), first.get('type', 'Unknown'),
                                 complexity=complexity)
        for t in valid_targets[1:]:
            generator.add_target(t.get('id'), t.get('name', 'Unknown'),
                                 t.get('number', 'N/A'), t.get('type', 'Unknown'))
        generator.process()
        cases.extend(generator.test_cases)

    for key in suites:
        builder = SUITE_BUILDERS.get(key)
        if builder:
            cases.extend(builder())

    # Renumber sequentially so IDs stay contiguous across the crawl and all suites.
    for idx, case in enumerate(cases, start=1):
        case["test_id"] = f"UAT-{idx:03d}"

    return cases
