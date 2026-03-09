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
        self.test_cases.append({
            "test_id": f"UAT-{self.counter:04d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        self.counter += 1

    def _resolve_target(self, action_type, rule_obj):
        """Resolves target names and IDs based on RC action types mapped from the Answering Rule payload."""
        if action_type == 'TransferToExtension':
            t_id = str(rule_obj.get('transfer', {}).get('extension', {}).get('id', ''))
            t_name = self.ext_map.get(t_id, {}).get('name', f"ID: {t_id}")
            t_ext = self.ext_map.get(t_id, {}).get('ext', '')
            return f"Transfer -> {t_name} (Ext {t_ext})", t_id
            
        elif action_type == 'TakeMessagesReturnToGreeting' or action_type == 'Voicemail':
            t_id = str(rule_obj.get('voicemail', {}).get('recipient', {}).get('id', ''))
            t_name = self.ext_map.get(t_id, {}).get('name', f"ID: {t_id}")
            return f"Voicemail -> {t_name}", t_id
            
        elif action_type == 'UnconditionalForwarding':
            num = rule_obj.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown')
            return f"External Forward -> {num}", None
            
        elif action_type == 'PlayAnnouncementOnly':
            return "Play Announcement & Disconnect", None
            
        elif action_type == 'Bypass':
            return "Bypass Queue -> Next Action", None
            
        return f"Action: {action_type}", None

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
            # 1. PHONE NUMBERS (Strict matching to prevent site DIDs)
            # ---------------------------------------------------------
            dids = []
            ph_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/phone-number', method='GET', raise_error=False)
            if ph_resp and 'records' in ph_resp:
                for r in ph_resp['records']:
                    if r.get('usageType') == 'DirectNumber' and str(r.get('extension', {}).get('id', '')) == str(cid):
                        dids.append(r.get('phoneNumber'))

            if cpath == "Primary Flow":
                if dids:
                    for did in dids:
                        self.add_case(f"{prefix}Integration", f"External Routing (DID: {did})", f"Dial exact DID {did} from an external mobile device.", f"Call successfully connects to {cname} via PSTN.")
                else:
                    self.add_case(f"{prefix}Integration", "Internal Routing", f"Dial extension {cext} internally.", f"Call successfully connects to {cname}.")

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
            # 3. ANSWERING RULES (Custom, After Hours, and Queue Details)
            # ---------------------------------------------------------
            ar_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{cid}/answering-rule', method='GET', raise_error=False)
            rules = ar_resp.get('records', []) if ar_resp else []

            for rule in rules:
                if not rule.get('enabled', False): continue
                rtype = rule.get('type')

                # --- CUSTOM RULES ---
                if rtype == 'Custom':
                    rname = rule.get('name', 'Custom Rule')
                    raction = rule.get('callHandlingAction', 'Unknown')
                    
                    # Accurately parse the exact triggers
                    conds = []
                    if rule.get('callers'):
                        c_ids = [c.get('callerId', c.get('name', 'Unknown')) for c in rule['callers']]
                        conds.append(f"Caller ID is {', '.join(c_ids)}")
                    if rule.get('calledNumbers'):
                        n_ids = [n.get('phoneNumber', 'Unknown') for n in rule['calledNumbers']]
                        conds.append(f"Dialed Number is {', '.join(n_ids)}")
                    if rule.get('schedule'): 
                        conds.append("Matches specific Custom Schedule")
                        
                    cond_str = " AND ".join(conds) if conds else "Unknown Condition"

                    tname, tid = self._resolve_target(raction, rule)
                    self.add_case(f"{prefix}Routing", f"Custom Rule Trigger: {rname}", f"{path_str}Initiate call where: {cond_str}.", f"Rule intercepts call. Executes [{raction}] -> {tname}.")
                    
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"Custom Rule '{rname}' from {cname}"})

                # --- AFTER HOURS ---
                elif rtype == 'AfterHours':
                    raction = rule.get('callHandlingAction', 'Unknown')
                    tname, tid = self._resolve_target(raction, rule)
                    self.add_case(f"{prefix}Routing", "After Hours Routing", f"{path_str}Initiate call OUTSIDE Business Hours: [{bh_str}].", f"Executes [{raction}] -> {tname}.")
                    
                    if tid and self.ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": tid, "name": self.ext_map[tid]['name'], "ext": self.ext_map[tid]['ext'], "type": self.ext_map[tid]['type'], "path": f"After Hours from {cname}"})

                # --- QUEUE CONFIGURATION (Embedded inside BusinessHours) ---
                elif rtype == 'BusinessHours' and ctype == 'Department':
                    
                    # Verify if Introductory Greeting actually exists and is enabled
                    greetings = rule.get('greetings', [])
                    intro_enabled = any(g.get('type') == 'Introductory' for g in greetings)
                    if intro_enabled:
                        self.add_case(f"{prefix}Queue Setup", "Introductory Greeting", f"{path_str}Place call into {cname}.", "Configured Introductory Greeting plays completely before ringing begins.")
                    
                    # Extract raw queue parameters
                    q = rule.get('queue', {})
                    if q:
                        tmode = q.get('transferMode', 'Simultaneous')
                        ag_timeout = q.get('agentTimeout', 0)
                        hold_time = q.get('holdTime', 0)
                        max_callers = q.get('maxCallers', 0)
                        wrap_up = q.get('wrapUpTime', 0)
                        int_mode = q.get('holdAudioInterruptionMode', 'Never')
                        int_per = q.get('holdAudioInterruptionPeriod', 0)

                        # Distribution
                        self.add_case(f"{prefix}Queue Setup", f"Distribution: {tmode}", f"Ensure agents are available. Place call into queue.", f"Call distributes based strictly on {tmode} logic.")
                        if tmode != 'Simultaneous' and ag_timeout > 0:
                            self.add_case(f"{prefix}Queue Setup", f"Agent Ring Timeout ({ag_timeout}s)", f"Targeted agent ignores call for {ag_timeout} seconds.", f"Timer expires. Call immediately hunts to the next available agent.")

                        # Agent Timers
                        if wrap_up > 0:
                            self.add_case(f"{prefix}Queue Setup", f"Wrap-Up Timer ({wrap_up}s)", f"Agent completes a queue call. Place another call into queue.", f"Agent enters Wrap-Up status and does NOT receive call until {wrap_up}s ACW timer expires.")

                        # Interrupt Audio
                        if int_mode != 'Never' and int_per > 0:
                            self.add_case(f"{prefix}Queue Setup", f"Interrupt Audio ({int_per}s)", f"Remain on hold in {cname} for > {int_per} seconds.", f"At {int_per}s, hold music pauses, interrupt prompt plays, then hold music resumes.")

                        # Boundaries & Overflows
                        self.add_case(f"{prefix}Overflows", "Zero Agents Available", f"Log ALL agents out of {cname} or set DND. Initiate call.", "Call bypasses queue entirely and instantly triggers Max Wait Time / No Answer routing.")

                        # Max Wait Time
                        if hold_time > 0:
                            h_act = q.get('holdTimeExpirationAction', 'Unknown')
                            h_name, h_id = self._resolve_target(h_act, rule) 
                            self.add_case(f"{prefix}Overflows", f"Max Wait Time Limit ({hold_time}s)", f"Remain on hold in {cname} for exactly {hold_time} seconds.", f"Timer expires. Call is removed from queue and executes [{h_act}] -> {h_name}.")
                            if h_id and self.ext_map.get(h_id, {}).get('type') in ['Department', 'IvrMenu']:
                                self.queue_to_process.append({"id": h_id, "name": self.ext_map[h_id]['name'], "ext": self.ext_map[h_id]['ext'], "type": self.ext_map[h_id]['type'], "path": f"Wait Time Overflow from {cname}"})
                        
                        # Max Callers Limit
                        if max_callers > 0:
                            m_act = q.get('maxCallersAction', 'Unknown')
                            m_name, m_id = self._resolve_target(m_act, rule)
                            self.add_case(f"{prefix}Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {cname} with {max_callers} calls. Dial call #{max_callers + 1}.", f"Call #{max_callers + 1} is blocked from queue and executes [{m_act}] -> {m_name}.")
                            if m_id and self.ext_map.get(m_id, {}).get('type') in ['Department', 'IvrMenu']:
                                self.queue_to_process.append({"id": m_id, "name": self.ext_map[m_id]['name'], "ext": self.ext_map[m_id]['ext'], "type": self.ext_map[m_id]['type'], "path": f"Max Callers Overflow from {cname}"})

                        # Zero-Out (Always maps to the voicemail recipient defined in the rule)
                        z_name, _ = self._resolve_target('Voicemail', rule)
                        self.add_case(f"{prefix}Overflows", "Zero-Out (DTMF '0')", f"While listening to {cname} hold music, press '0' on the dialpad.", f"Call escapes the queue and routes to Voicemail Recipient: {z_name}.")

            # ---------------------------------------------------------
            # 4. IVR MENU (Dynamic Key Mapping)
            # ---------------------------------------------------------
            if ctype == 'IvrMenu':
                ivr = rc_api_call(f'/restapi/v1.0/ivr-menus/{cid}', method='GET', raise_error=False) or {}
                if 'prompt' in ivr:
                    ptext = ivr['prompt'].get('text', 'Audio File')
                    self.add_case(f"{prefix}IVR Tests", "Greeting Playback", f"Dial {cname}.", f"Prompt plays clearly: '{ptext}'.")

                for act in ivr.get('actions', []):
                    key = act.get('input', '')
                    if not key: continue
                    a_type = act.get('action', 'Unknown')
                    
                    t_name = "Unknown"
                    t_id = None
                    if 'extension' in act:
                        t_id = str(act['extension'].get('id', ''))
                        t_name = f"{self.ext_map.get(t_id, {}).get('name', f'ID {t_id}')} (Ext {self.ext_map.get(t_id, {}).get('ext', '')})"
                    elif 'phoneNumber' in act:
                        t_name = f"External Number: {act['phoneNumber']}"

                    self.add_case(f"{prefix}IVR Routing", f"Key Mapping: '{key}'", f"{path_str}Listen to prompt and press '{key}'.", f"Registers DTMF. Executes [{a_type}] -> {t_name}.")
                    
                    if t_id and self.ext_map.get(t_id, {}).get('type') in ['Department', 'IvrMenu']:
                        self.queue_to_process.append({"id": t_id, "name": self.ext_map[t_id]['name'], "ext": self.ext_map[t_id]['ext'], "type": self.ext_map[t_id]['type'], "path": f"Key '{key}' from {cname}"})

                self.add_case(f"{prefix}IVR Boundaries", "Invalid Key Press", f"Press an unassigned key in {cname}.", "System plays 'Invalid entry' prompt and replays menu.")


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Entry point for the UI router."""
    generator = UATGenerator(extension_id, extension_name, extension_number, extension_type)
    generator.process()
    return generator.test_cases
