from webapp.rc_api import rc_api_call

# =============================================================================
# TAILORED & HOLISTIC UAT GENERATOR
# Every test uses EXACT data. Only tests CONFIGURED features.
# =============================================================================

def safe_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    return val if isinstance(val, dict) else {}

def safe_list(d, key):
    if not isinstance(d, dict): return []
    val = d.get(key)
    return val if isinstance(val, list) else []


class TailoredUATGenerator:
    """
    Generates UAT cases tailored to ACTUAL queue configuration.
    Uses exact phone numbers, agent names, configured values.
    Only tests features that are actually configured.
    """
    
    def __init__(self, start_id, start_name, start_number, start_type):
        self.test_cases = []
        self.test_counter = 1
        self.processed_extensions = set()
        self.processing_queue = []
        
        # Build extension directory
        self.ext_directory = self._build_extension_directory()
        
        self.processing_queue.append({
            'id': str(start_id),
            'name': start_name,
            'number': start_number,
            'type': start_type,
            'path': [],
            'depth': 0,
            'context': 'Primary Entry Point'
        })
    
    def _build_extension_directory(self):
        """Build complete extension directory"""
        directory = {}
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
        
        if isinstance(resp, dict):
            for ext in safe_list(resp, 'records'):
                if isinstance(ext, dict) and ext.get('id'):
                    ext_id = str(ext.get('id'))
                    contact = safe_dict(ext, 'contact')
                    directory[ext_id] = {
                        'id': ext_id,
                        'name': ext.get('name', 'Unknown'),
                        'number': ext.get('extensionNumber', 'N/A'),
                        'type': ext.get('type', 'Unknown'),
                        'email': contact.get('email'),
                        'first_name': contact.get('firstName'),
                        'last_name': contact.get('lastName')
                    }
        
        return directory
    
    def add_test(self, category, scenario, action, expected):
        """Add test case"""
        self.test_cases.append({
            'test_id': f'UAT-{self.test_counter:04d}',
            'category': category,
            'scenario': scenario,
            'action': action,
            'expected': expected
        })
        self.test_counter += 1
    
    def _format_phone(self, number):
        """Format phone number as (XXX) XXX-XXXX"""
        if not number:
            return ''
        clean = number.replace('+1', '').replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
        if len(clean) == 10:
            return f"({clean[:3]}) {clean[3:6]}-{clean[6:]}"
        elif len(clean) == 11 and clean[0] == '1':
            return f"({clean[1:4]}) {clean[4:7]}-{clean[7:]}"
        return number
    
    def _get_phone_numbers(self, ext_id):
        """Get verified phone numbers for extension"""
        numbers = []
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
        
        if isinstance(resp, dict):
            for rec in safe_list(resp, 'records'):
                if not isinstance(rec, dict):
                    continue
                # VERIFY belongs to this extension
                ext_obj = safe_dict(rec, 'extension')
                if str(ext_obj.get('id', '')) != str(ext_id):
                    continue
                phone = rec.get('phoneNumber')
                if phone:
                    numbers.append({
                        'raw': phone,
                        'formatted': self._format_phone(phone),
                        'usage': rec.get('usageType', 'Unknown'),
                        'type': rec.get('type', '')
                    })
        return numbers
    
    def _get_business_hours(self, ext_id):
        """Get business hours"""
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
        if not isinstance(resp, dict) or not safe_dict(resp, 'schedule'):
            resp = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
        
        if not isinstance(resp, dict):
            return {'is_24_7': True, 'ranges': {}}
        
        weekly = safe_dict(safe_dict(resp, 'schedule'), 'weeklyRanges')
        if not weekly:
            return {'is_24_7': True, 'ranges': {}}
        
        ranges = {}
        for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
            if day in weekly:
                times = weekly[day]
                if isinstance(times, list) and times:
                    ranges[day] = []
                    for tr in times:
                        if isinstance(tr, dict):
                            ranges[day].append({'from': tr.get('from', ''), 'to': tr.get('to', '')})
        
        return {'is_24_7': False, 'ranges': ranges} if ranges else {'is_24_7': True, 'ranges': {}}
    
    def _get_rules(self, ext_id):
        """Get answering rules"""
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
        rules = {'bh': None, 'ah': None, 'custom': []}
        
        if isinstance(resp, dict):
            for rule in safe_list(resp, 'records'):
                if isinstance(rule, dict) and rule.get('enabled'):
                    rtype = rule.get('type')
                    if rtype == 'BusinessHours':
                        rules['bh'] = rule
                    elif rtype == 'AfterHours':
                        rules['ah'] = rule
                    elif rtype == 'Custom':
                        rules['custom'].append(rule)
        return rules
    
    def _get_queue_config(self, ext_id, bh_rule):
        """Get ACTUAL queue configuration - only what's configured"""
        q_api = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/call-queue-info', method='GET', raise_error=False) or {}
        q_settings = safe_dict(bh_rule, 'queue') if bh_rule else {}
        
        # Get transfer mode (REQUIRED field)
        transfer_mode = q_api.get('transferMode') or q_settings.get('transferMode') or 'Rotating'
        
        config = {
            'transfer_mode': transfer_mode,
            'agent_timeout': None,
            'wrap_up': None,
            'hold_time': None,
            'max_callers': None,
            'interrupt': None,
            'max_concurrent': None,
            'agents': [],
            'overflows': {},
            'greetings': {},
            'recording': None
        }
        
        # Agent timeout - ONLY if NOT simultaneous AND configured
        if transfer_mode.lower() != 'simultaneous':
            timeout = q_api.get('agentTimeout') or q_settings.get('agentTimeout')
            if timeout and int(timeout) > 0:
                config['agent_timeout'] = int(timeout)
        
        # Wrap-up time - ONLY if configured
        wrap = q_api.get('wrapUpTime') or q_settings.get('wrapUpTime')
        if wrap and int(wrap) > 0:
            config['wrap_up'] = int(wrap)
        
        # Hold time - ONLY if configured
        hold = q_settings.get('holdTime') or q_api.get('holdTime')
        if hold and int(hold) > 0:
            config['hold_time'] = int(hold)
        
        # Max callers - ONLY if configured
        max_c = q_settings.get('maxCallers') or q_api.get('maxCallers')
        if max_c and int(max_c) > 0:
            config['max_callers'] = int(max_c)
        
        # Interrupt period - ONLY if configured
        intr = q_api.get('holdAudioInterruptionPeriod') or q_settings.get('holdAudioInterruptionPeriod')
        if intr and int(intr) > 0:
            config['interrupt'] = int(intr)
        
        # Max concurrent per agent
        max_conc = q_settings.get('maxCallersPerAgent') or q_api.get('maxCallersPerAgent')
        if max_conc and int(max_conc) > 0:
            config['max_concurrent'] = int(max_conc)
        
        # Get agents with full details
        agents = q_api.get('fixedOrderAgents', []) or q_api.get('agents', [])
        for agent in agents:
            if isinstance(agent, dict):
                ext_obj = safe_dict(agent, 'extension')
                agent_id = str(ext_obj.get('id', ''))
                if agent_id in self.ext_directory:
                    config['agents'].append(self.ext_directory[agent_id])
        
        # Greetings - check what's actually configured
        if bh_rule:
            for greet in safe_list(bh_rule, 'greetings'):
                if isinstance(greet, dict):
                    gtype = greet.get('type')
                    if gtype == 'Introductory':
                        config['greetings']['intro'] = True
                    elif gtype == 'ConnectingAudio':
                        config['greetings']['connecting'] = True
                    elif gtype == 'InterruptPrompt':
                        config['greetings']['interrupt_audio'] = True
        
        # Recording
        if bh_rule:
            rec = safe_dict(bh_rule, 'callRecording')
            if rec and rec.get('enabled'):
                config['recording'] = rec.get('mode', 'Automatic')
        
        # Overflows - extract actual destinations
        for api_key, label in [('noAnswerAction', 'no_agents'), ('holdTimeExpirationAction', 'max_wait'), ('maxCallersAction', 'queue_full')]:
            action = q_settings.get(api_key)
            if action:
                tid, tname = self._extract_target(action)
                if tid or tname != 'Unknown':
                    config['overflows'][label] = {'id': tid, 'name': tname}
        
        return config
    
    def _extract_target(self, action):
        """Extract routing target"""
        if not isinstance(action, dict):
            return None, 'Unknown'
        
        # Extension
        ext = safe_dict(action, 'extension') or safe_dict(safe_dict(action, 'transfer'), 'extension')
        if ext and ext.get('id'):
            tid = str(ext.get('id'))
            if tid in self.ext_directory:
                info = self.ext_directory[tid]
                return tid, f"{info['name']} (Ext {info['number']})"
            return tid, f"Extension {tid}"
        
        # Voicemail
        vm = safe_dict(action, 'voicemail')
        recip = safe_dict(vm, 'recipient')
        if recip and recip.get('id'):
            tid = str(recip.get('id'))
            if tid in self.ext_directory:
                return tid, f"Voicemail: {self.ext_directory[tid]['name']}"
            return tid, 'Voicemail'
        
        # External
        fwd = safe_dict(action, 'unconditionalForwarding')
        if fwd and fwd.get('phoneNumber'):
            return None, f"External: {self._format_phone(fwd.get('phoneNumber'))}"
        
        return None, 'Unknown'
    
    def process_all_flows(self):
        """Main processing"""
        while self.processing_queue:
            curr = self.processing_queue.pop(0)
            
            if curr['depth'] > 10:
                continue
            
            key = f"{curr['id']}:{curr['context']}"
            if key in self.processed_extensions:
                continue
            self.processed_extensions.add(key)
            
            path_display = ' → '.join(curr['path'] + [curr['name']]) if curr['path'] else curr['name']
            prefix = f"[{path_display}] " if curr['path'] else ""
            
            if curr['type'] == 'Department':
                self._gen_queue_tests(curr['id'], curr['name'], curr['number'], curr['path'], prefix, curr['depth'])
        
        self._add_global()
        return self.test_cases
    
    def _gen_queue_tests(self, qid, qname, qnum, path, prefix, depth):
        """Generate tailored queue tests using EXACT configuration"""
        
        # Extract ACTUAL data
        phones = self._get_phone_numbers(qid)
        hours = self._get_business_hours(qid)
        rules = self._get_rules(qid)
        cfg = self._get_queue_config(qid, rules['bh'])
        
        # ================================================================
        # INTEGRATION - Using EXACT phone numbers
        # ================================================================
        
        if depth == 0:
            # Internal
            self.add_test(
                f"{prefix}Integration",
                "Internal Extension Dialing",
                f"Using a desk phone or RingCentral app, dial extension {qnum}.",
                f"Device rings and connects to {qname}. Clear two-way audio. No errors."
            )
            
            # External - EVERY ACTUAL phone number
            for phone in phones:
                if phone['usage'] == 'DirectNumber':
                    self.add_test(
                        f"{prefix}Integration - PSTN",
                        f"External DID: {phone['formatted']}",
                        f"From your personal mobile phone (cellular network, NOT company WiFi), dial {phone['formatted']}.",
                        f"Call routes through PSTN to {qname}. Your mobile caller ID displays to agents. Clear audio both ways."
                    )
                elif phone['usage'] == 'MainCompanyNumber':
                    self.add_test(
                        f"{prefix}Integration - Main",
                        f"Main Number: {phone['formatted']}",
                        f"From external phone, dial {phone['formatted']}. Navigate IVR to {qname}.",
                        f"Reaches {qname} successfully."
                    )
            
            # If NO phone numbers found
            if not phones:
                self.add_test(
                    f"{prefix}Integration",
                    "No Direct Numbers Configured",
                    f"Verify {qname} configuration in Admin Portal.",
                    f"Note: No direct phone numbers assigned to {qname}. Access only via internal dialing or call transfer."
                )
            
            # Audio quality
            self.add_test(
                f"{prefix}Integration",
                "Sustained Audio Quality",
                f"Call {qname} (via extension {qnum}). Speak continuously for 2 minutes.",
                "Audio remains clear with no jitter, latency, or quality degradation."
            )
        
        # ================================================================
        # BUSINESS HOURS - Using EXACT schedule
        # ================================================================
        
        if not hours['is_24_7'] and hours['ranges']:
            # Get first configured day
            first_day = next(iter(hours['ranges'].keys()))
            first_time = hours['ranges'][first_day][0]
            
            self.add_test(
                f"{prefix}Time Routing",
                "Business Hours - Open",
                f"Place call on {first_day} at {first_time['from']} (start of business hours).",
                f"Follows business hours routing for {qname}."
            )
            
            self.add_test(
                f"{prefix}Time Routing",
                "Business Hours - Closed",
                f"Place call on Sunday at 11:00 PM (outside business hours).",
                f"Follows after-hours routing for {qname}."
            )
        
        # ================================================================
        # CALLER EXPERIENCE - Only configured features
        # ================================================================
        
        # Intro greeting - ONLY if configured
        if cfg['greetings'].get('intro'):
            self.add_test(
                f"{prefix}Queue - Caller Experience",
                "Introductory Greeting",
                f"Call {qname} (ext {qnum}). Listen to audio sequence.",
                "Intro greeting plays fully before agent ringing begins."
            )
        
        # Hold music - always applicable
        self.add_test(
            f"{prefix}Queue - Caller Experience",
            "Hold Music",
            f"Call {qname} (ext {qnum}) and wait in queue.",
            "Hold music plays continuously without gaps or distortion."
        )
        
        # Interrupt announcements - ONLY if configured
        if cfg['interrupt']:
            self.add_test(
                f"{prefix}Queue - Caller Experience",
                f"Periodic Announcements (every {cfg['interrupt']}s)",
                f"Remain on hold for {cfg['interrupt'] + 15} seconds.",
                f"Every {cfg['interrupt']}s, music pauses, announcement plays, music resumes."
            )
        
        # Recording announcement - ONLY if enabled
        if cfg['recording']:
            self.add_test(
                f"{prefix}Queue - Caller Experience",
                f"Recording Announcement ({cfg['recording']} mode)",
                f"Call {qname} (ext {qnum}).",
                f"Announcement 'This call may be recorded' plays before agent connection. Mode: {cfg['recording']}."
            )
        
        # ================================================================
        # AGENT TESTS - Using ACTUAL agent names
        # ================================================================
        
        if cfg['agents']:
            # Use first actual agent
            agent = cfg['agents'][0]
            aname = f"{agent['name']} (Ext {agent['number']})"
            
            self.add_test(
                f"{prefix}Queue - Agent Tests",
                "Agent Opt-In",
                f"{aname} enables 'Accept Queue Calls' for {qname}. Call ext {qnum}.",
                f"{aname}'s device rings. Caller ID shows '{qname}'."
            )
            
            self.add_test(
                f"{prefix}Queue - Agent Tests",
                "Agent Opt-Out",
                f"{aname} disables 'Accept Queue Calls'. Call ext {qnum}.",
                f"{aname} does NOT ring. Call routes to next agent."
            )
            
            self.add_test(
                f"{prefix}Queue - Agent Tests",
                "Agent DND",
                f"{aname} sets Do Not Disturb. Call ext {qnum}.",
                f"{aname} does NOT ring. Treated as unavailable."
            )
            
            self.add_test(
                f"{prefix}Queue - Agent Tests",
                "Agent Decline",
                f"While ringing {aname}, agent clicks Decline.",
                f"Ringing stops. Call hunts to next agent."
            )
            
            self.add_test(
                f"{prefix}Queue - Agent Tests",
                "Agent Already Busy",
                f"{aname} is on another call. Call ext {qnum}.",
                "Busy agent does NOT ring. Routes to available agents."
            )
            
            # Wrap-up - ONLY if configured
            if cfg['wrap_up']:
                self.add_test(
                    f"{prefix}Queue - Agent Tests",
                    f"After-Call Work ({cfg['wrap_up']}s)",
                    f"{aname} completes call. Immediately call ext {qnum} again.",
                    f"{aname} in wrap-up for {cfg['wrap_up']}s. Does NOT ring during this time."
                )
        else:
            # No agents configured
            self.add_test(
                f"{prefix}Queue - Config Check",
                "No Agents Assigned",
                f"Verify {qname} agent configuration in Admin Portal.",
                f"WARNING: No agents assigned to {qname}. All calls will overflow immediately."
            )
        
        # ================================================================
        # DISTRIBUTION - Using ACTUAL mode
        # ================================================================
        
        self.add_test(
            f"{prefix}Queue - Distribution",
            f"Mode: {cfg['transfer_mode']}",
            f"Ensure 2+ agents available. Call ext {qnum}.",
            f"Distributes per {cfg['transfer_mode']} logic."
        )
        
        # Agent timeout - ONLY if applicable
        if cfg['agent_timeout']:
            self.add_test(
                f"{prefix}Queue - Distribution",
                f"Agent Timeout ({cfg['agent_timeout']}s)",
                f"First agent ignores call for {cfg['agent_timeout']}s.",
                f"After {cfg['agent_timeout']}s, hunts to next agent."
            )
        
        # ================================================================
        # CALL HANDLING - Always applicable
        # ================================================================
        
        self.add_test(
            f"{prefix}Queue - Call Handling",
            "Hold",
            f"Agent answers from {qname}. Clicks Hold.",
            "Caller hears hold music. Agent can retrieve."
        )
        
        self.add_test(
            f"{prefix}Queue - Call Handling",
            "Warm Transfer",
            f"Agent answers from {qname}. Warm transfer to ext 101.",
            "Consults, then completes transfer successfully."
        )
        
        self.add_test(
            f"{prefix}Queue - Call Handling",
            "Blind Transfer",
            f"Agent answers from {qname}. Blind transfer to ext 101.",
            "Agent released immediately. Caller transferred."
        )
        
        # ================================================================
        # OVERFLOWS - ONLY configured ones with ACTUAL destinations
        # ================================================================
        
        # No agents overflow
        if 'no_agents' in cfg['overflows']:
            dest = cfg['overflows']['no_agents']
            self.add_test(
                f"{prefix}Queue - Overflow",
                "No Agents Available",
                f"All {qname} agents log out or set DND. Call ext {qnum}.",
                f"Bypasses queue. Routes to: {dest['name']}"
            )
            
            # Recursively process overflow destination
            if dest['id'] and dest['id'] in self.ext_directory:
                dinfo = self.ext_directory[dest['id']]
                if dinfo['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': dest['id'],
                        'name': dinfo['name'],
                        'number': dinfo['number'],
                        'type': dinfo['type'],
                        'path': path + [qname],
                        'depth': depth + 1,
                        'context': 'No Agents Overflow'
                    })
        
        # Max wait overflow - ONLY if configured
        if cfg['hold_time'] and 'max_wait' in cfg['overflows']:
            dest = cfg['overflows']['max_wait']
            mins = cfg['hold_time'] // 60
            secs = cfg['hold_time'] % 60
            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
            
            self.add_test(
                f"{prefix}Queue - Overflow",
                f"Max Wait Time ({time_str})",
                f"Remain on hold in {qname} for {cfg['hold_time']}s ({time_str}).",
                f"At {time_str}, removed from queue. Routes to: {dest['name']}"
            )
            
            # Recursive
            if dest['id'] and dest['id'] in self.ext_directory:
                dinfo = self.ext_directory[dest['id']]
                if dinfo['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': dest['id'],
                        'name': dinfo['name'],
                        'number': dinfo['number'],
                        'type': dinfo['type'],
                        'path': path + [qname],
                        'depth': depth + 1,
                        'context': f'Max Wait ({time_str}) Overflow'
                    })
        
        # Queue full overflow - ONLY if configured
        if cfg['max_callers'] and 'queue_full' in cfg['overflows']:
            dest = cfg['overflows']['queue_full']
            self.add_test(
                f"{prefix}Queue - Overflow",
                f"Queue Full ({cfg['max_callers']} max)",
                f"Flood {qname} with {cfg['max_callers']} calls. Place call #{cfg['max_callers'] + 1}.",
                f"Call #{cfg['max_callers'] + 1} rejected. Routes to: {dest['name']}"
            )
            
            # Recursive
            if dest['id'] and dest['id'] in self.ext_directory:
                dinfo = self.ext_directory[dest['id']]
                if dinfo['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': dest['id'],
                        'name': dinfo['name'],
                        'number': dinfo['number'],
                        'type': dinfo['type'],
                        'path': path + [qname],
                        'depth': depth + 1,
                        'context': f'Queue Full ({cfg["max_callers"]}) Overflow'
                    })
        
        # ================================================================
        # QUALITY TESTS
        # ================================================================
        
        self.add_test(
            f"{prefix}Quality",
            "DTMF Recognition",
            f"During call from {qname}, press keys 0-9.",
            "All DTMF tones transmitted and recognized clearly."
        )
        
        self.add_test(
            f"{prefix}Quality",
            "Background Noise Handling",
            f"During {qname} call, introduce moderate background noise.",
            "Speech remains clear. Noise appropriately suppressed."
        )
    
    def _add_global(self):
        """Global validation tests"""
        
        self.add_test(
            "Global Validation",
            "Call Logs",
            "Admin Portal > Analytics > Call Log.",
            "All test calls logged with correct details."
        )
        
        self.add_test(
            "Global Validation",
            "Call Recordings",
            "Admin Portal > Call Recordings (if enabled).",
            "Recordings available and playable."
        )
        
        self.add_test(
            "Global Validation",
            "Queue Analytics",
            "Analytics > Queue Performance.",
            "Metrics update in real-time."
        )


def get_testable_extensions():
    """Get testable extensions"""
    resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not isinstance(resp, dict):
        return []
    
    valid = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [
        {
            "id": e.get('id'),
            "name": e.get('name', 'Unnamed'),
            "extensionNumber": e.get('extensionNumber', 'N/A'),
            "type": e.get('type')
        }
        for e in safe_list(resp, 'records')
        if isinstance(e, dict) and e.get('type') in valid
    ]
    return sorted(entities, key=lambda x: x['name'])


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """
    Generate tailored UAT cases using EXACT queue configuration.
    Every test uses actual phone numbers, agent names, configured values.
    Only tests features that are actually configured.
    """
    gen = TailoredUATGenerator(extension_id, extension_name, extension_number, extension_type)
    return gen.process_all_flows()
