from webapp.rc_api import rc_api_call

# =============================================================================
# DATA EXTRACTION HELPERS
# =============================================================================

def get_testable_extensions():
    """Fetches base call flows for the UI dropdown. Excludes standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [
        {"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']}
        for ext in response['records'] if ext.get('type') in valid_types
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
        if resp and 'records' in resp:
            for e in resp['records']:
                m[str(e['id'])] = {
                    'name': e.get('name', 'Unknown'),
                    'ext': e.get('extensionNumber', 'N/A'),
                    'type': e.get('type', 'Unknown')
                }
        return m

    def add_case(self, category, scenario, action, expected):
        """Helper to append a UAT test case to the list."""
        self.test_cases.append({
            "test_id": f"UAT-{self.counter:04d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        self.counter += 1

    def _extract_rule_conditions(self, rule):
        """Accurately parses the exact triggers for a custom rule from the JSON arrays."""
        conditions = []
        if rule.get('callers'):
            c_ids = [c.get('callerId', c.get('name', 'Unknown')) for c in rule['callers']]
            conditions.append(f"Caller ID is {', '.join(c_ids)}")
        if rule.get('calledNumbers'):
            n_ids = [n.get('phoneNumber', 'Unknown') for n in rule['calledNumbers']]
            conditions.append(f"Dialed Number is {', '.join(n_ids)}")
        if rule.get('schedule'):
            conditions.append("Matches Custom Schedule")
        
        return " AND ".join(conditions) if conditions else "Specific Configured Condition"

    def _resolve_target(self, rule_obj, action_name="Unknown Action"):
        """Forensically digs through standard RingCentral routing objects to find the true destination."""
        if not rule_obj:
            return "Disconnect / System Default", None
            
        # 1. Check for Extension Transfer
        if 'transfer' in rule_obj and 'extension' in rule_obj['transfer']:
            tid = str(rule_obj['transfer']['extension'].get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}")
            text = self.ext_map.get(tid, {}).get('ext', '')
            return f"Transfer -> {tname} (Ext {text})", tid
            
        # 2. Check for Voicemail Transfer
        if 'voicemail' in rule_obj and 'recipient' in rule_obj['voicemail']:
            tid = str(rule_obj['voicemail']['recipient'].get('id', ''))
            tname = self.ext_map.get(tid, {}).get('name', f"Extension {tid}")
            return f"Voicemail -> {tname}", tid
            
        # 3. Check for External Forwarding
        if 'unconditionalForwarding' in rule_obj:
            num = rule_obj['unconditionalForwarding'].get('phoneNumber', 'Unknown Number')
            return f"External Forward -> {num}", None
            
        # 4. Fallback to the configured action string (e.g., 'PlayAnnouncementOnly')
        extracted_action = rule_obj.get('callHandlingAction', action_name)
        return f"Action: {extracted_action}", None

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
            # 1. PHONE NUMBERS
            # ---------------------------------------------------------
            dids = []
            ph_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/phone-number', method='GET', raise_error=False)
            if ph_resp and 'records' in ph_resp:
                for r in ph_resp['records']:
                    if r.get('usageType') not in ['ExtensionNumber', 'MainCompanyNumber']:
                        dids.append(r.get('phoneNumber'))

            if cpath == "Primary Flow":
                self.add_case(f"{prefix}Integration", "Internal Routing", f"Dial extension {cext} internally.", f"Call successfully connects to {cname}.")
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}Integration", f"External Routing (DID)", f"Dial the assigned DID {did} from an external mobile device.", f"Call successfully connects to {cname} via PSTN.")
                else:
                    self.add_case(f"{prefix}Integration", "External Routing (No DID)", f"Dial the Main Company Number and enter extension {cext}.", f"Call successfully routes to {cname}.")

            # ---------------------------------------------------------
            # 2. BUSINESS HOURS
            # ---------------------------------------------------------
            bh_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/business-hours', method='GET', raise_error=False)
            bh_str = "24/7 (Always Open)"
            
            if bh_resp and 'schedule' in bh_resp:
                sched = bh_resp['schedule']
                if 'weeklyRanges' in sched:
                    days = [f"{d[:3]} {t[0].get('from')}-{t[0].get('to')}" for d, t in sched['weeklyRanges'].items() if t]
                    if days: bh_str = ", ".join(days)
            elif not bh_resp:
                # Inherit from account if Queue has no custom schedule
                acc_bh = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
                if acc_bh and 'schedule' in acc_bh and 'weeklyRanges' in acc_bh['schedule']:
                     days = [f"{d[:3]} {t[0].get('from')}-{t[0].get('to')}" for d, t in acc_bh['schedule']['weeklyRanges'].items() if t]
                     if days: bh_str = ", ".join(days)

            self.add_case(f"{prefix}Routing", "Business Hours (In-Hours)", f"{path_str}Initiate call during Open Hours: [{bh_str}].", f"Call follows standard Business Hours routing for {cname}.")

            # ---------------------------------------------------------
            # 3. ANSWERING RULES (Custom, After Hours, Queue Overflows)
            # ---------------------------------------------------------
            ar_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules = ar_resp.get('records', []) if ar_resp else []
            
            queue_settings = {}
            has_intro = False
            has_after_hours = False

            for rule in rules:
                if not rule.get('enabled', False): continue
                rtype = rule.get('type')

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    cond_str = self._extract_rule_conditions(rule)
                    tname, tid = self._resolve_target(rule)
                    
                    self.add_case(f"{prefix}Routing", f"Custom Rule Trigger: {rname}", f"{path_str}Initiate call where: {cond_str}.", f"Rule intercepts call. Executes -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}'"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    has_after_hours = True
                    tname, tid = self._resolve_target(rule)
                    
                    self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours: [{bh_str}].", f"Executes After Hours logic. Routes to -> {tname}.")
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours Routing"})

                # --- QUEUE SETTINGS ---
                elif rtype == 'BusinessHours' and ctype == 'Department':
                    queue_settings = rule.get('queue', {})
                    if any(g.get('type') == 'Introductory' for g in rule.get('greetings', [])):
                        has_intro = True

            # If no After Hours rule explicitly defined, note the default override
            if not has_after_hours and bh_str != "24/7 (Always Open)":
                 self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours: [{bh_str}].", f"Follows default account After Hours logic.")

            # ---------------------------------------------------------
            # 4. CALL QUEUE EXHAUSTIVE TESTING
            # ---------------------------------------------------------
            if ctype == 'Department':
                # Fetch auxiliary agent details
                q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/call-queue-info', method='GET', raise_error=False) or {}
                
                # Merge configurations from /answering-rule and /call-queue-info
                tmode = q_info.get('transferMode', queue_settings.get('transferMode', 'Simultaneous'))
                ag_timeout = q_info.get('agentTimeout', queue_settings.get('agentTimeout', '[Configured Timeout]'))
                wrap_up = q_info.get('wrapUpTime', queue_settings.get('wrapUpTime', '[Configured ACW]'))
                hold_time = queue_settings.get('holdTime', '[Configured Wait Time]')
                max_callers = queue_settings.get('maxCallers', '[Configured Limit]')
                int_per = q_info.get('holdAudioInterruptionPeriod', '[Configured Period]')

                # --- Queue Experience ---
                self.add_case(f"{prefix}Queue Experience", "Introductory Greeting", f"{path_str}Place call to {cname}.", "Intro Greeting plays fully before agent ringing begins." if has_intro else "Call immediately begins ringing agents or playing hold music.")
                self.add_case(f"{prefix}Queue Experience", "Connecting Audio", f"Remain on hold in {cname}.", "Configured Hold Music plays cleanly without distortion.")
                self.add_case(f"{prefix}Queue Experience", f"Interrupt Audio", f"Remain on hold for at least {int_per} seconds.", "Hold music pauses, interrupt prompt plays, then music resumes.")

                # --- Agent Management ---
                self.add_case(f"{prefix}Agent Tests", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON. Place test call.", f"Agent's device rings. Queue Name '{cname}' prepends Caller ID.")
                self.add_case(f"{prefix}Agent Tests", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place test call.", "Agent's device does NOT ring. Call smoothly hunts to next available agent.")
                self.add_case(f"{prefix}Agent Tests", "Active Call Decline", "While call is ringing an agent, agent clicks 'Decline'.", "Ringing stops immediately for that agent. Call hunts to next available agent.")
                self.add_case(f"{prefix}Agent Tests", "Agent Busy", "Agent is on an active outbound call. Place new call to queue.", "Queue call hunts to next agent or waits; does not interrupt active call.")
                self.add_case(f"{prefix}Agent Tests", f"Wrap-Up (ACW) Timer", f"Agent completes queue call. Immediately place a second call.", f"Agent enters Wrap-Up status and does NOT ring again until {wrap_up}s timer expires.")

                # --- Distribution Logic ---
                self.add_case(f"{prefix}Distribution", f"Routing: {tmode}", f"Ensure multiple agents are 'Available'. Place call.", f"Call distributes based on {tmode} logic.")
                if str(tmode).lower() != 'simultaneous':
                    self.add_case(f"{prefix}Distribution", f"Ring Timeout", f"Targeted agent ignores call for {ag_timeout} seconds.", "Timer expires. Call drops from Agent 1 and rings Agent 2.")

                # --- Call Handling ---
                self.add_case(f"{prefix}Call Handling", "Call Hold", "Agent answers queue call and places caller on hold via RC App.", "Caller hears agent hold music. Call can be retrieved successfully.")
                self.add_case(f"{prefix}Call Handling", "Warm Transfer", "Agent answers, initiates Warm Transfer to internal extension, consults, and completes.", "Caller is connected to secondary extension with two-way audio.")
                self.add_case(f"{prefix}Call Handling", "Blind Transfer", "Agent answers and initiates Blind Transfer to internal extension.", "Agent is released. Caller hears ringing to secondary extension.")
                self.add_case(f"{prefix}Call Handling", "Call Park", "Agent answers and Parks the call to a Park Location (e.g., *801).", "Caller is parked. Call can be retrieved by dialing the park code.")

                # --- Boundaries & Overflows ---
                # Forensically extract targets using merged rule object
                hold_target_name, h_id = self._resolve_target({'transfer': queue_settings.get('transfer'), 'voicemail': queue_settings.get('voicemail')}, queue_settings.get('holdTimeExpirationAction', 'Unknown'))
                max_target_name, m_id = self._resolve_target({'transfer': queue_settings.get('transfer'), 'voicemail': queue_settings.get('voicemail')}, queue_settings.get('maxCallersAction', 'Unknown'))
                
                # Zero-out maps natively to the Voicemail recipient in RC queues
                zero_name = "Default Operator/Voicemail"
                if queue_settings.get('voicemail') and queue_settings['voicemail'].get('recipient'):
                    z_id = str(queue_settings['voicemail']['recipient']['id'])
                    zero_name = f"Voicemail of {self.ext_map.get(z_id, {}).get('name', z_id)}"

                self.add_case(f"{prefix}Queue Boundaries", "Zero Agents Logged In", f"Log ALL agents out of {cname} or set DND. Initiate call.", f"Call bypasses queue ringing and executes Overflow -> {hold_target_name}.")
                
                self.add_case(f"{prefix}Queue Boundaries", f"Max Wait Time ({hold_time}s)", f"Remain on hold in {cname} for {hold_time} seconds.", f"Timer expires. Call is removed from queue and executes -> {hold_target_name}.")
                if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu']:
                    self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": "Max Wait Time Overflow"})

                self.add_case(f"{prefix}Queue Boundaries", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {cname} with {max_callers} calls. Dial an additional call.", f"The additional call is blocked from queue and executes -> {max_target_name}.")
                if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu']:
                    self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": "Max Callers Overflow"})

                self.add_case(f"{prefix}Queue Boundaries", "Zero-Out (Press 0)", f"While listening to {cname} hold music, press '0' on the dialpad.", f"Call escapes the queue and routes to -> {zero_name}.")

            # ---------------------------------------------------------
            # 5. IVR MENU TESTING
            # ---------------------------------------------------------
            elif ctype == 'IvrMenu':
                ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False) or {}
                
                ptext = ivr_info.get('prompt', {}).get('text', 'Configured Audio File')
                self.add_case(f"{prefix}IVR Tests", "Greeting Playback", f"Dial {cname}.", f"Prompt plays clearly: '{ptext}'.")
                self.add_case(f"{prefix}IVR Tests", "Barge-In (Interruptibility)", f"While greeting is playing, press a valid menu key.", "IVR registers DTMF immediately and routes call without forcing caller to listen to full greeting.")
                
                actions = ivr_info.get('actions', [])
                if actions:
                    for act in actions:
                        key = act.get('input', '')
                        if not key: continue
                        
                        # Use our robust resolver for IVR keys
                        tname, tid = self._resolve_target(act, act.get('action', 'Unknown'))
                        self.add_case(f"{prefix}IVR Routing", f"Key Mapping: '{key}'", f"Listen to prompt and press '{key}'.", f"Routes call to -> {tname}.")
                        
                        if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                            self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"IVR Key '{key}'"})
                else:
                    self.add_case(f"{prefix}IVR Routing", "Menu Keys", "Press configured keys on the dialpad.", "System routes call to corresponding configured destinations.")
                
                self.add_case(f"{prefix}IVR Boundaries", "Dial-By-Extension", f"Enter a known user's 3 or 4-digit extension.", "IVR intercepts the string and transfers call directly to the user.")
                self.add_case(f"{prefix}IVR Boundaries", "Invalid Key Press", f"Press an unassigned key (e.g., '9' or '#').", "System plays 'Invalid entry' prompt and replays menu.")
                self.add_case(f"{prefix}IVR Boundaries", "Timeout (No Input)", f"Listen to entire prompt and provide no input.", "System times out, replays menu, and executes default timeout routing.")

        # ---------------------------------------------------------
        # 6. GLOBAL ADMINISTRATION TASKS (Appended once at the end)
        # ---------------------------------------------------------
        self.add_case("Global Administration", "Voicemail Deposit", "Trigger any tested routing scenario that routes to Voicemail. Leave a 15-second test message.", "Correct Voicemail greeting plays. Message is successfully recorded without truncating.")
        self.add_case("Global Administration", "Voicemail Delivery", "Check the designated target's inbox (Email Notification or RingCentral App).", "The voicemail audio file is delivered accurately.")
        self.add_case("Global Administration", "Call Logs Generation", "Log into the Admin Portal and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct originating Caller ID, target extensions, duration, and final result across the entire traced journey.")


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Entry point for the UI router."""
    generator = UATGenerator(extension_id, extension_name, extension_number, extension_type)
    generator.process()
    return generator.test_cases
