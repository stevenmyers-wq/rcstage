from webapp.rc_api import rc_api_call

# =============================================================================
# COMPREHENSIVE UAT GENERATOR - 50-100+ TESTS PER QUEUE
# No shortcuts. Every scenario tested.
# =============================================================================

def safe_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    return val if isinstance(val, dict) else {}

def safe_list(d, key):
    if not isinstance(d, dict): return []
    val = d.get(key)
    return val if isinstance(val, list) else []


class ComprehensiveUATGenerator:
    """Generates 50-100+ comprehensive test cases per queue"""
    
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
                        'status': ext.get('status', 'Unknown'),
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
    
    def _format_phone_number(self, number):
        """Format phone number"""
        if not number:
            return ''
        
        clean = number.replace('+1', '').replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
        
        if len(clean) == 10:
            return f"({clean[:3]}) {clean[3:6]}-{clean[6:]}"
        elif len(clean) == 11 and clean[0] == '1':
            return f"({clean[1:4]}) {clean[4:7]}-{clean[7:]}"
        
        return number
    
    def _extract_phone_numbers(self, ext_id):
        """Extract ALL phone numbers for extension"""
        numbers = []
        
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number',
            method='GET',
            raise_error=False
        )
        
        if isinstance(resp, dict):
            for record in safe_list(resp, 'records'):
                if not isinstance(record, dict):
                    continue
                
                # Verify belongs to this extension
                ext_obj = safe_dict(record, 'extension')
                if str(ext_obj.get('id', '')) != str(ext_id):
                    continue
                
                phone = record.get('phoneNumber')
                if phone:
                    numbers.append({
                        'phone': phone,
                        'formatted': self._format_phone_number(phone),
                        'usage_type': record.get('usageType', 'Unknown'),
                        'type': record.get('type', 'Unknown')
                    })
        
        return numbers
    
    def _extract_business_hours(self, ext_id):
        """Extract business hours"""
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours',
            method='GET',
            raise_error=False
        )
        
        if not isinstance(resp, dict) or not safe_dict(resp, 'schedule'):
            resp = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
        
        if not isinstance(resp, dict):
            return {'is_24_7': True, 'display': '24/7', 'ranges': {}}
        
        schedule = safe_dict(resp, 'schedule')
        weekly = safe_dict(schedule, 'weeklyRanges')
        
        if not weekly:
            return {'is_24_7': True, 'display': '24/7', 'ranges': {}}
        
        day_ranges = {}
        display_parts = []
        
        for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
            if day in weekly:
                times = weekly[day]
                if isinstance(times, list) and times:
                    day_ranges[day] = []
                    for time_range in times:
                        if isinstance(time_range, dict):
                            from_time = time_range.get('from', '')
                            to_time = time_range.get('to', '')
                            if from_time and to_time:
                                day_ranges[day].append({'from': from_time, 'to': to_time})
                                display_parts.append(f"{day[:3]} {from_time}-{to_time}")
        
        return {
            'is_24_7': False,
            'display': ', '.join(display_parts) if display_parts else 'Custom',
            'ranges': day_ranges
        }
    
    def _extract_answering_rules(self, ext_id):
        """Extract answering rules"""
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule',
            method='GET',
            raise_error=False
        )
        
        rules = {'business_hours': None, 'after_hours': None, 'custom': []}
        
        if isinstance(resp, dict):
            for rule in safe_list(resp, 'records'):
                if isinstance(rule, dict) and rule.get('enabled'):
                    rule_type = rule.get('type')
                    if rule_type == 'BusinessHours':
                        rules['business_hours'] = rule
                    elif rule_type == 'AfterHours':
                        rules['after_hours'] = rule
                    elif rule_type == 'Custom':
                        rules['custom'].append(rule)
        
        return rules
    
    def _extract_queue_config(self, ext_id, bh_rule):
        """Extract queue configuration"""
        queue_api = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/call-queue-info',
            method='GET',
            raise_error=False
        ) or {}
        
        queue_settings = safe_dict(bh_rule, 'queue') if bh_rule else {}
        
        config = {
            'transfer_mode': queue_api.get('transferMode') or queue_settings.get('transferMode') or 'Rotating',
            'agent_timeout': None,
            'wrap_up_time': None,
            'hold_time': None,
            'max_callers': None,
            'interrupt_period': None,
            'agents': [],
            'overflow_actions': {},
            'greetings': {}
        }
        
        # Agent timeout - only if NOT simultaneous
        timeout = queue_api.get('agentTimeout') or queue_settings.get('agentTimeout')
        if timeout and int(timeout) > 0 and config['transfer_mode'].lower() != 'simultaneous':
            config['agent_timeout'] = int(timeout)
        
        # Wrap-up time
        wrap_up = queue_api.get('wrapUpTime') or queue_settings.get('wrapUpTime')
        if wrap_up and int(wrap_up) > 0:
            config['wrap_up_time'] = int(wrap_up)
        
        # Hold time
        hold = queue_settings.get('holdTime') or queue_api.get('holdTime')
        if hold and int(hold) > 0:
            config['hold_time'] = int(hold)
        
        # Max callers
        max_c = queue_settings.get('maxCallers') or queue_api.get('maxCallers')
        if max_c and int(max_c) > 0:
            config['max_callers'] = int(max_c)
        
        # Interrupt period
        interrupt = queue_api.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod')
        if interrupt and int(interrupt) > 0:
            config['interrupt_period'] = int(interrupt)
        
        # Agents
        agents = queue_api.get('fixedOrderAgents', []) or queue_api.get('agents', [])
        for agent in agents:
            if isinstance(agent, dict):
                ext_obj = safe_dict(agent, 'extension')
                agent_id = str(ext_obj.get('id', ''))
                if agent_id and agent_id in self.ext_directory:
                    config['agents'].append(self.ext_directory[agent_id])
        
        # Greetings
        if bh_rule:
            for greeting in safe_list(bh_rule, 'greetings'):
                if isinstance(greeting, dict):
                    g_type = greeting.get('type')
                    if g_type == 'Introductory':
                        config['greetings']['has_intro'] = True
                    elif g_type == 'InterruptPrompt':
                        config['greetings']['has_interrupt'] = True
        
        # Overflow actions
        for key, label in [('noAnswerAction', 'no_agents'), ('holdTimeExpirationAction', 'max_wait'), ('maxCallersAction', 'max_callers')]:
            action = queue_settings.get(key)
            if action:
                target_id, target_name = self._extract_target(action)
                config['overflow_actions'][label] = {'target_id': target_id, 'target_name': target_name}
        
        return config
    
    def _extract_target(self, action_obj):
        """Extract routing target"""
        if not isinstance(action_obj, dict):
            return None, 'Configured Destination'
        
        ext = safe_dict(action_obj, 'extension')
        if not ext:
            ext = safe_dict(safe_dict(action_obj, 'transfer'), 'extension')
        
        if ext and ext.get('id'):
            target_id = str(ext.get('id'))
            if target_id in self.ext_directory:
                info = self.ext_directory[target_id]
                return target_id, f"{info['name']} (Ext {info['number']})"
            return target_id, f"Extension {target_id}"
        
        voicemail = safe_dict(action_obj, 'voicemail')
        recipient = safe_dict(voicemail, 'recipient')
        if recipient and recipient.get('id'):
            target_id = str(recipient.get('id'))
            if target_id in self.ext_directory:
                info = self.ext_directory[target_id]
                return target_id, f"Voicemail of {info['name']}"
            return target_id, 'Voicemail'
        
        forwarding = safe_dict(action_obj, 'unconditionalForwarding')
        if forwarding and forwarding.get('phoneNumber'):
            phone = forwarding.get('phoneNumber')
            return None, f"External: {self._format_phone_number(phone)}"
        
        return None, 'Configured Destination'
    
    def process_all_flows(self):
        """Main processing loop"""
        while self.processing_queue:
            current = self.processing_queue.pop(0)
            
            ext_id = current['id']
            ext_name = current['name']
            ext_number = current['number']
            ext_type = current['type']
            path = current['path']
            depth = current['depth']
            context = current['context']
            
            if depth > 10:
                continue
            
            process_key = f"{ext_id}:{context}"
            if process_key in self.processed_extensions:
                continue
            self.processed_extensions.add(process_key)
            
            path_display = ' → '.join(path + [ext_name]) if path else ext_name
            path_prefix = f"[{path_display}] " if path else ""
            
            if ext_type == 'Department':
                self._generate_comprehensive_queue_tests(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
            
        self._add_global_tests()
        return self.test_cases
    
    def _generate_comprehensive_queue_tests(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate COMPREHENSIVE queue tests - 50-100+ tests"""
        
        # Extract data
        numbers = self._extract_phone_numbers(ext_id)
        hours = self._extract_business_hours(ext_id)
        rules = self._extract_answering_rules(ext_id)
        queue_config = self._extract_queue_config(ext_id, rules.get('business_hours'))
        
        # =================================================================
        # SECTION 1: INTEGRATION & CONNECTIVITY (5-10 tests)
        # =================================================================
        
        if depth == 0:
            # Internal dialing
            self.add_test(
                f"{path_prefix}Integration",
                "Internal Extension Dialing",
                f"Using a desk phone or RingCentral app logged into the company account, dial extension {ext_number}.",
                f"Call connects to {ext_name} with clear two-way audio. No SIP errors or dead air."
            )
            
            # PSTN routing - EVERY phone number
            for num in numbers:
                if num['usage_type'] == 'DirectNumber':
                    self.add_test(
                        f"{path_prefix}Integration - PSTN",
                        f"External Direct Number: {num['formatted']}",
                        f"From your personal mobile phone (NOT connected to company network), dial {num['formatted']}. Use your actual mobile carrier.",
                        f"Call routes through PSTN to {ext_name}. Caller ID displays your mobile number. Clear two-way audio established. No voice quality issues."
                    )
                elif num['usage_type'] == 'MainCompanyNumber':
                    self.add_test(
                        f"{path_prefix}Integration - Main",
                        f"Main Company Number: {num['formatted']}",
                        f"From external phone, dial main company number {num['formatted']}. Follow auto-attendant prompts to reach {ext_name}.",
                        f"Successfully connects to {ext_name} after IVR navigation."
                    )
            
            # Audio quality sustained test
            self.add_test(
                f"{path_prefix}Integration",
                "Sustained Audio Quality",
                f"Establish call to {ext_name}. Maintain active conversation for 3 full minutes with continuous speech.",
                "Audio quality remains consistent throughout. No jitter, latency spikes, packet loss, or degradation over time."
            )
            
            # Caller ID display
            self.add_test(
                f"{path_prefix}Integration",
                "Caller ID Display Accuracy",
                f"Place call to {ext_name} from known number. Observe caller ID on agent device.",
                f"Caller ID accurately displays: Queue name '{ext_name}' prepended to actual caller information."
            )
        
        # =================================================================
        # SECTION 2: TIME-BASED ROUTING (3-5 tests)
        # =================================================================
        
        if not hours['is_24_7'] and hours['ranges']:
            first_day = next(iter(hours['ranges'].keys()))
            first_range = hours['ranges'][first_day][0] if hours['ranges'][first_day] else None
            
            if first_range:
                self.add_test(
                    f"{path_prefix}Time Routing",
                    "Business Hours - During Open Hours",
                    f"Place test call on {first_day} at {first_range['from']} (within configured hours: {hours['display']}).",
                    f"Call follows Business Hours routing path. Plays business hours greeting (if configured). Routes to queue agents."
                )
                
                self.add_test(
                    f"{path_prefix}Time Routing",
                    "Business Hours - After Hours",
                    f"Place test call on Sunday at 11:00 PM or {first_day} at 11:00 PM (outside configured hours: {hours['display']}).",
                    f"Call follows After Hours routing path. Does NOT route to queue. Executes after hours action."
                )
                
                self.add_test(
                    f"{path_prefix}Time Routing",
                    "Business Hours - Edge Case (Boundary)",
                    f"Place test call exactly at {first_range['to']} (end of business hours).",
                    "System correctly classifies call as either inside or outside hours based on exact time configuration."
                )
        
        # Holiday routing
        self.add_test(
            f"{path_prefix}Time Routing",
            "Holiday Schedule Override",
            "Temporarily configure a holiday schedule for today in Admin Portal. Place test call.",
            "Holiday schedule takes precedence over standard business hours. Call follows holiday routing."
        )
        
        # =================================================================
        # SECTION 3: CALLER EXPERIENCE (5-10 tests)
        # =================================================================
        
        # Introductory greeting
        if queue_config.get('greetings', {}).get('has_intro'):
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                "Introductory Greeting Playback",
                f"Place call to {ext_name}. Listen carefully to audio sequence.",
                "Introductory greeting plays in full BEFORE any agent ringing begins. Greeting is not cut off or interrupted."
            )
        
        # Hold music
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Hold Music / Comfort Audio",
            f"Place call to {ext_name}. Remain in queue while agents are unavailable or busy.",
            "Configured hold music or comfort message plays continuously without gaps, dead air, or distortion."
        )
        
        # Music on hold quality
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Hold Music Quality Sustained",
            f"Remain in {ext_name} queue for 2+ minutes.",
            "Hold music loops smoothly without audio artifacts, volume fluctuations, or quality degradation."
        )
        
        # Interrupt announcements
        if queue_config.get('interrupt_period'):
            period = queue_config['interrupt_period']
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                f"Periodic Announcements ({period} second intervals)",
                f"Remain on hold for at least {period + 20} seconds. Time the intervals.",
                f"Every {period} seconds, hold music pauses, comfort announcement plays ('Please continue holding...'), then music resumes seamlessly."
            )
        
        # Call recording announcement
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Call Recording Announcement (if enabled)",
            f"Place call to {ext_name}.",
            "If call recording is enabled, announcement 'This call may be recorded' plays before agent connection. If not enabled, no announcement plays."
        )
        
        # Queue position announcement (if configured)
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Queue Position Announcement (if enabled)",
            f"Place call to {ext_name} while other callers are already in queue.",
            "If queue position announcements enabled, system announces position in queue (e.g., 'You are caller number 3')."
        )
        
        # Estimated wait time (if configured)
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Estimated Wait Time Announcement (if enabled)",
            f"Place call to {ext_name} during busy period.",
            "If estimated wait time enabled, system announces approximate wait time based on current queue conditions."
        )
        
        # =================================================================
        # SECTION 4: AGENT BEHAVIOR & STATUS (10-15 tests)
        # =================================================================
        
        agent_name = "the queue agent"
        if queue_config.get('agents'):
            first_agent = queue_config['agents'][0]
            agent_name = f"{first_agent['name']} (Ext {first_agent['number']})"
        
        # Agent opt-in
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Queue Opt-In",
            f"Have {agent_name} log into RingCentral app. Enable 'Accept Queue Calls' for {ext_name}. Verify status shows 'Available'. Place test call.",
            f"Agent's device rings immediately. Caller ID displays queue name '{ext_name}' prepended to caller information."
        )
        
        # Agent opt-out
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Queue Opt-Out",
            f"Have {agent_name} disable 'Accept Queue Calls' for {ext_name}. Status should show they're not accepting queue calls. Place test call.",
            f"Agent's device does NOT ring. Call immediately hunts to next available agent in queue. No ringing occurs for opted-out agent."
        )
        
        # Agent DND
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Do Not Disturb Status",
            f"Have {agent_name} set status to 'Do Not Disturb' in RingCentral app. Place test call to {ext_name}.",
            f"Agent does NOT ring. System treats agent as unavailable. Call routes to other available agents."
        )
        
        # Agent available but away
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent 'Away' Status",
            f"Have {agent_name} set presence status to 'Away' but keep queue calls enabled. Place test call.",
            "Agent DOES ring (queue calls override 'Away' status). Call routes normally to agent."
        )
        
        # Agent decline call
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Active Decline",
            f"While queue call is ringing {agent_name}, have agent click 'Decline' button.",
            f"Ringing stops immediately for {agent_name}. Call seamlessly hunts to next available agent without caller experiencing disruption."
        )
        
        # Agent ignore (let ring)
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Ignore (No Answer)",
            f"While queue call is ringing {agent_name}, have agent neither answer nor decline. Let it ring.",
            f"After configured ring timeout, call automatically moves to next agent. Agent who ignored is marked as missed call."
        )
        
        # Agent on active call
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Already on Active Call",
            f"Have {agent_name} place or answer a different call (make them busy). While busy, place new call to {ext_name}.",
            f"System recognizes {agent_name} as busy. Queue call does NOT interrupt active call. Routes to other available agents."
        )
        
        # Agent on hold with another call
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent with Call on Hold",
            f"Have {agent_name} answer a call and place it on hold. While call is on hold, place new queue call.",
            f"System may route queue call to {agent_name} OR treat as busy (depends on 'Max concurrent calls per agent' setting). Behavior is consistent with configuration."
        )
        
        # Wrap-up time
        if queue_config.get('wrap_up_time'):
            wrap_up = queue_config['wrap_up_time']
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                f"After-Call Work Period ({wrap_up} seconds)",
                f"Have {agent_name} answer queue call and complete it. Within 1 second of completing, place second queue call to {ext_name}.",
                f"Agent {agent_name} enters 'Wrap-Up' status for {wrap_up} seconds. Does NOT ring for new queue calls during this period. After {wrap_up} seconds, becomes available again."
            )
            
            # Wrap-up manual override
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                f"Wrap-Up Manual Release",
                f"During wrap-up period, have {agent_name} manually set status back to 'Available' before timer expires.",
                "Agent immediately becomes available for new queue calls. Wrap-up timer is cancelled. Next queue call routes to agent."
            )
        
        # Agent logs out
        self.add_test(
            f"{path_prefix}Queue - Agent Tests",
            "Agent Logout During Queue Call",
            f"While queue call is ringing {agent_name}, have agent log out of RingCentral app.",
            "Ringing stops immediately. Call hunts to next available agent. Logged-out agent no longer receives queue calls."
        )
        
        # =================================================================
        # SECTION 5: DISTRIBUTION LOGIC (5-10 tests)
        # =================================================================
        
        transfer_mode = queue_config.get('transfer_mode', 'Rotating')
        
        # Distribution mode verification
        self.add_test(
            f"{path_prefix}Queue - Distribution",
            f"Distribution Mode: {transfer_mode}",
            f"Ensure at least 2 agents are available and opted-in to {ext_name}. Note which agents are available. Place test call.",
            f"Call distributes according to {transfer_mode} logic. Rotating: calls next agent in sequence. Simultaneous: rings all agents at once. Sequential: always starts with first agent."
        )
        
        # Multi-agent simultaneous (if mode is simultaneous)
        if transfer_mode.lower() == 'simultaneous':
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                "Simultaneous Ring - Multiple Agents",
                f"Ensure 3+ agents available. Place call to {ext_name}.",
                "ALL available agents' devices ring simultaneously at the exact same time. First to answer gets the call."
            )
            
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                "Simultaneous Ring - First to Answer Wins",
                f"During simultaneous ring, have one agent answer while others are still ringing.",
                "Answering agent connects to caller. All other agents' devices stop ringing immediately. No duplicate connections."
            )
        
        # Agent timeout (if applicable)
        if queue_config.get('agent_timeout'):
            timeout = queue_config['agent_timeout']
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                f"Agent Ring Timeout ({timeout} seconds)",
                f"Place call to {ext_name}. Have first agent ignore call (no answer, no decline). Use stopwatch to time exactly {timeout} seconds.",
                f"After exactly {timeout} seconds, call stops ringing first agent and immediately hunts to next agent per distribution mode. Timer is precise."
            )
            
            # Verify timeout doesn't happen too early
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                f"Agent Ring Timeout - Premature Answer Prevention",
                f"Place call to {ext_name}. Have agent answer at {timeout - 5} seconds (before timeout).",
                "Agent successfully connects to caller. Timeout does NOT trigger. Connection is immediate."
            )
        
        # Call hunting sequence
        self.add_test(
            f"{path_prefix}Queue - Distribution",
            "Call Hunting Sequence",
            f"Have first 2 agents decline or ignore call. Ensure 3rd agent is available. Place call.",
            "Call hunts through agents in correct sequence. After first 2 declines/timeouts, reaches 3rd agent and rings successfully."
        )
        
        # All agents busy
        self.add_test(
            f"{path_prefix}Queue - Distribution",
            "All Agents Busy Scenario",
            f"Have all {ext_name} agents become busy with other calls. Place new queue call.",
            "Call enters queue and holds. Caller hears hold music. When first agent becomes available, call routes to them immediately."
        )
        
        # =================================================================
        # SECTION 6: CALL HANDLING & FEATURES (10-12 tests)
        # =================================================================
        
        # Hold function
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Place Call on Hold",
            f"Agent answers queue call. Agent clicks 'Hold' button in RingCentral app.",
            "Caller immediately hears configured on-hold music. Agent's line is released. Agent can perform other tasks."
        )
        
        # Retrieve from hold
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Retrieve Call from Hold",
            f"Agent has queue call on hold. Agent clicks 'Resume' or retrieves the call.",
            "Call is immediately retrieved. Two-way audio resumes. On-hold music stops for caller."
        )
        
        # Hold for extended period
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Extended Hold Duration",
            f"Agent places queue call on hold for 2+ minutes.",
            "Caller continues hearing hold music. No disconnection occurs. Call remains stable. Agent can retrieve at any time."
        )
        
        # Warm transfer initiate
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer - Initiation",
            f"Agent answers queue call. Agent initiates warm transfer to another extension (e.g., supervisor extension 102).",
            "Original caller is placed on hold automatically. Transfer destination begins ringing. Agent can hear ring-back tone."
        )
        
        # Warm transfer consultation
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer - Consultation",
            f"During warm transfer, have transfer destination answer. Agent consults with them while original caller on hold.",
            "Agent can speak privately with transfer destination. Original caller remains on hold hearing hold music. Caller cannot hear consultation."
        )
        
        # Warm transfer completion
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer - Completion",
            f"After consultation, agent clicks 'Complete Transfer' button.",
            "Original caller is connected to transfer destination with clear two-way audio. Transferring agent is released from call. Call does not drop."
        )
        
        # Warm transfer cancellation
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer - Cancellation",
            f"Agent initiates warm transfer but clicks 'Cancel' before completing.",
            "Transfer is cancelled. Original caller is reconnected to original agent. No call drop occurs."
        )
        
        # Blind transfer
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Blind Transfer",
            f"Agent answers queue call. Agent initiates blind transfer to another extension without consultation.",
            "Transferring agent is released immediately. Caller hears ringing to destination extension. If destination answers, caller connects. If no answer, call may return to queue or voicemail."
        )
        
        # Call park
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Call Park",
            f"Agent answers queue call. Agent parks call to park location *801.",
            "Caller is parked successfully and hears hold music. System announces park location to agent. Agent can hang up."
        )
        
        # Call park retrieve
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Call Park Retrieval",
            f"With call parked at *801, have another user dial *801.",
            "Parked call is retrieved successfully. Retrieving user connects to caller with two-way audio."
        )
        
        # Recording (if enabled)
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Call Recording (if enabled)",
            f"Place call to {ext_name} and have agent answer. Complete the call.",
            "If automatic recording enabled, call is recorded from start to finish. Recording captures both caller and agent audio clearly."
        )
        
        # Call conferencing
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Add to Conference",
            f"Agent answers queue call. Agent adds another participant to create conference call.",
            "Conference is created successfully. All parties can hear each other. Audio quality remains high."
        )
        
        # =================================================================
        # SECTION 7: OVERFLOW & BOUNDARY CONDITIONS (8-15 tests)
        # =================================================================
        
        # No agents available
        if 'no_agents' in queue_config.get('overflow_actions', {}):
            overflow = queue_config['overflow_actions']['no_agents']
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                "No Agents Available",
                f"Ensure ALL {ext_name} agents are: logged out, on DND, or have queue calls disabled. Verify no agents show as available. Place test call.",
                f"Call immediately bypasses queue (does not play hold music). Executes no-answer overflow action. Routes to: {overflow['target_name']}"
            )
        
        # Max wait time
        if queue_config.get('hold_time') and 'max_wait' in queue_config.get('overflow_actions', {}):
            hold_time = queue_config['hold_time']
            overflow = queue_config['overflow_actions']['max_wait']
            
            minutes = hold_time // 60
            seconds = hold_time % 60
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Maximum Wait Time ({time_str})",
                f"Place call to {ext_name}. Ensure no agents answer. Remain on hold with stopwatch. Time exactly {hold_time} seconds ({time_str}).",
                f"At {hold_time} second mark, hold music stops. Call is removed from queue. Executes wait time overflow action. Routes to: {overflow['target_name']}"
            )
            
            # Max wait time - early answer prevention
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Max Wait Time - Answer Before Expiry",
                f"Place call to {ext_name}. Have agent answer at {hold_time - 10} seconds (before max wait expires).",
                "Agent successfully connects to caller. Max wait timeout does NOT trigger. Call connects normally."
            )
        
        # Max callers capacity
        if queue_config.get('max_callers') and 'max_callers' in queue_config.get('overflow_actions', {}):
            max_callers = queue_config['max_callers']
            overflow = queue_config['overflow_actions']['max_callers']
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Queue Capacity Limit ({max_callers} maximum)",
                f"Flood {ext_name} with {max_callers} simultaneous calls. While all {max_callers} are in queue or connected, place call #{max_callers + 1}.",
                f"Call #{max_callers + 1} is rejected. Does NOT enter queue. Immediately executes max callers overflow action. Routes to: {overflow['target_name']}"
            )
            
            # Capacity - call within limit
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Queue Capacity - Within Limit",
                f"Place {max_callers - 1} calls to {ext_name}. While queue has {max_callers - 1} calls, place one more call.",
                "Call enters queue successfully. No overflow triggered. Caller hears hold music normally."
            )
        
        # DTMF zero-out
        self.add_test(
            f"{path_prefix}Queue - Overflow",
            "DTMF Zero-Out (Press 0)",
            f"While on hold in {ext_name}, press '0' on telephone keypad.",
            "If zero-out configured, call escapes queue immediately and routes to operator/voicemail. If not configured, keypress is ignored gracefully."
        )
        
        # Invalid DTMF
        self.add_test(
            f"{path_prefix}Queue - Overflow",
            "Invalid DTMF Input",
            f"While on hold in {ext_name}, press various keys like *, #, 5, 9.",
            "Invalid DTMF inputs are ignored. Caller remains in queue. Hold music continues. No errors or disruptions."
        )
        
        # Caller hangup
        self.add_test(
            f"{path_prefix}Queue - Boundaries",
            "Caller Abandons (Hangup)",
            f"Place call to {ext_name}. While in queue before agent answers, hang up.",
            "Call is removed from queue immediately. Queue statistics record abandoned call. No orphaned connections."
        )
        
        # Network disruption
        self.add_test(
            f"{path_prefix}Queue - Boundaries",
            "Network Disruption Handling",
            f"Place call to {ext_name}. During call (while connected or in queue), temporarily disrupt network (e.g., disable WiFi for 5 seconds, then re-enable).",
            "System handles disruption gracefully. Call either reconnects or disconnects cleanly. No ghost calls or stuck sessions."
        )
        
        # =================================================================
        # SECTION 8: QUALITY & PERFORMANCE (3-5 tests)
        # =================================================================
        
        # DTMF tone recognition
        self.add_test(
            f"{path_prefix}Quality",
            "DTMF Tone Recognition",
            f"After agent answers queue call, press various keys (0-9, *, #).",
            "All DTMF tones are transmitted clearly and recognized accurately by receiving system (if applicable)."
        )
        
        # Background noise handling
        self.add_test(
            f"{path_prefix}Quality",
            "Background Noise Suppression",
            f"Place call to {ext_name}. During call, introduce moderate background noise (typing, conversation, music).",
            "Primary speech remains clear and intelligible. Noise is suppressed appropriately without excessive distortion."
        )
        
        # Echo test
        self.add_test(
            f"{path_prefix}Quality",
            "Echo Detection",
            f"During active queue call, have both parties speak simultaneously and listen for echo.",
            "No noticeable echo or feedback. Both parties can hear each other clearly without their own voice echoing back."
        )
        
        # Volume consistency
        self.add_test(
            f"{path_prefix}Quality",
            "Volume Level Consistency",
            f"During queue call, compare volume levels of: hold music, announcements, and live agent voice.",
            "All audio elements have consistent, appropriate volume levels. No sudden loud/quiet jumps. Smooth audio experience."
        )
        
        # =================================================================
        # RECURSIVE OVERFLOW PROCESSING
        # =================================================================
        
        # Add overflow destinations to processing queue for full recursive testing
        for label, overflow in queue_config.get('overflow_actions', {}).items():
            target_id = overflow.get('target_id')
            if target_id and target_id in self.ext_directory:
                dest = self.ext_directory[target_id]
                if dest['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': target_id,
                        'name': dest['name'],
                        'number': dest['number'],
                        'type': dest['type'],
                        'path': path + [ext_name],
                        'depth': depth + 1,
                        'context': f"{label.replace('_', ' ').title()} Overflow"
                    })
    
    def _add_global_tests(self):
        """Add global validation tests"""
        
        self.add_test(
            "Global Validation",
            "Call Logs - Accuracy",
            "Log into RingCentral Admin Portal. Navigate to Analytics > Reports > Call Log. Search for test calls.",
            "All test calls appear in logs with correct: date/time, caller ID, destination extension, call duration, disposition (answered/voicemail/abandoned/transferred)."
        )
        
        self.add_test(
            "Global Validation",
            "Call Logs - Real-time Updates",
            "Place test call. Immediately after call ends, refresh call log.",
            "Call appears in log within 1-2 minutes of completion. Data is accurate and complete."
        )
        
        self.add_test(
            "Global Validation",
            "Call Recording - Availability",
            "If call recording enabled, navigate to Admin Portal > Phone System > Call Recording.",
            "Test call recordings appear in list. Files are downloadable. Playback works correctly."
        )
        
        self.add_test(
            "Global Validation",
            "Call Recording - Audio Quality",
            "Download and play call recording.",
            "Recording captures both caller and agent audio clearly. No distortion or missing segments. Duration matches actual call time."
        )
        
        self.add_test(
            "Global Validation",
            "Voicemail - Delivery",
            "Check voicemail recipient's email inbox and RingCentral app.",
            "Voicemail audio file (.mp3/.wav) delivered successfully. Email subject and body contain correct details. Timestamp accurate."
        )
        
        self.add_test(
            "Global Validation",
            "Voicemail - Transcription",
            "If transcription enabled, check voicemail email or app notification.",
            "Voice-to-text transcription included. Transcription is reasonably accurate (allows for normal speech recognition limitations)."
        )
        
        self.add_test(
            "Global Validation",
            "Queue Analytics - Live Reports",
            "Navigate to Analytics > Live Reports > Queue Performance.",
            "Test queue calls update metrics in real-time: Service Level %, Average Wait Time, Abandoned Calls, Agents Available."
        )
        
        self.add_test(
            "Global Validation",
            "Queue Analytics - Historical Reports",
            "Run historical queue report for period covering test calls.",
            "Report accurately reflects test call activity. Metrics calculated correctly. Charts/graphs display properly."
        )


def get_testable_extensions():
    """Get testable extensions"""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not isinstance(response, dict):
        return []
    
    records = safe_list(response, 'records')
    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    
    entities = [
        {
            "id": ext.get('id'),
            "name": ext.get('name', 'Unnamed'),
            "extensionNumber": ext.get('extensionNumber', 'N/A'),
            "type": ext.get('type')
        }
        for ext in records 
        if isinstance(ext, dict) and ext.get('type') in valid_types
    ]
    
    return sorted(entities, key=lambda x: x['name'])


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """
    Generate 50-100+ comprehensive UAT test cases.
    Takes 1-2 minutes but produces complete coverage.
    """
    generator = ComprehensiveUATGenerator(
        extension_id,
        extension_name,
        extension_number,
        extension_type
    )
    
    return generator.process_all_flows()
