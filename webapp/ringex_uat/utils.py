from webapp.rc_api import rc_api_call
import time

# =============================================================================
# TRULY FORENSIC UAT GENERATOR - NO DEFAULTS, ALL REAL DATA
# Takes time to extract everything correctly. No shortcuts.
# =============================================================================

def safe_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    return val if isinstance(val, dict) else {}

def safe_list(d, key):
    if not isinstance(d, dict): return []
    val = d.get(key)
    return val if isinstance(val, list) else []


class TrulyForensicAnalyzer:
    """
    Extracts REAL data from APIs with no defaults or assumptions.
    If it takes 1-2 minutes of API calls, that's acceptable for accuracy.
    """
    
    def __init__(self, start_id, start_name, start_number, start_type):
        self.test_cases = []
        self.test_counter = 1
        self.processed_extensions = set()
        self.processing_queue = []
        
        print(f"[INIT] Building extension directory...")
        self.ext_directory = self._build_extension_directory()
        print(f"[INIT] Directory built: {len(self.ext_directory)} extensions")
        
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
        """Format phone number for display"""
        if not number:
            return ''
        
        # Remove +1 prefix if present
        clean = number.replace('+1', '').replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
        
        # Format as (XXX) XXX-XXXX if 10 digits
        if len(clean) == 10:
            return f"({clean[:3]}) {clean[3:6]}-{clean[6:]}"
        elif len(clean) == 11 and clean[0] == '1':
            # Handle 1-XXX-XXX-XXXX
            return f"({clean[1:4]}) {clean[4:7]}-{clean[7:]}"
        
        return number
    
    def _extract_phone_numbers_aggressive(self, ext_id):
        """
        AGGRESSIVELY extract ALL phone numbers.
        Returns list of dicts with full details.
        CRITICAL: Only returns numbers that ACTUALLY belong to this extension.
        """
        print(f"[PHONE] Extracting phone numbers for extension {ext_id}...")
        
        numbers = []
        
        # Call phone number API
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number',
            method='GET',
            raise_error=False
        )
        
        if not isinstance(resp, dict):
            print(f"[PHONE] WARNING: No phone number data returned for {ext_id}")
            return numbers
        
        records = safe_list(resp, 'records')
        print(f"[PHONE] Found {len(records)} phone number records")
        
        for record in records:
            if not isinstance(record, dict):
                continue
            
            # CRITICAL: Verify this number belongs to THIS extension
            ext_obj = safe_dict(record, 'extension')
            record_ext_id = str(ext_obj.get('id', ''))
            
            if record_ext_id != str(ext_id):
                print(f"[PHONE] Skipping number - belongs to extension {record_ext_id}, not {ext_id}")
                continue
            
            phone = record.get('phoneNumber')
            if not phone:
                continue
            
            usage_type = record.get('usageType', 'Unknown')
            phone_type = record.get('type', 'Unknown')
            label = record.get('label', '')
            
            formatted = self._format_phone_number(phone)
            
            print(f"[PHONE] Found: {formatted} ({usage_type})")
            
            numbers.append({
                'phone': phone,
                'formatted': formatted,
                'usage_type': usage_type,
                'type': phone_type,
                'label': label
            })
        
        print(f"[PHONE] Total verified numbers for {ext_id}: {len(numbers)}")
        return numbers
    
    def _extract_business_hours_aggressive(self, ext_id):
        """
        Extract business hours with no defaults.
        Returns actual schedule or clearly marks as 24/7.
        """
        print(f"[HOURS] Extracting business hours for extension {ext_id}...")
        
        # Try extension-specific first
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours',
            method='GET',
            raise_error=False
        )
        
        source = 'extension-specific'
        
        # If no extension hours, try account level
        if not isinstance(resp, dict) or not safe_dict(resp, 'schedule'):
            print(f"[HOURS] No extension hours, trying account level...")
            resp = rc_api_call(
                '/restapi/v1.0/account/~/business-hours',
                method='GET',
                raise_error=False
            )
            source = 'account-level'
        
        # If still no data, it's truly 24/7
        if not isinstance(resp, dict):
            print(f"[HOURS] No hours configured - defaulting to 24/7")
            return {
                'is_24_7': True,
                'display': '24/7 (Always Open)',
                'source': 'default',
                'ranges': {}
            }
        
        schedule = safe_dict(resp, 'schedule')
        weekly = safe_dict(schedule, 'weeklyRanges')
        
        if not weekly:
            print(f"[HOURS] No weekly ranges - 24/7")
            return {
                'is_24_7': True,
                'display': '24/7 (Always Open)',
                'source': source,
                'ranges': {}
            }
        
        # Build day ranges
        day_ranges = {}
        display_parts = []
        
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        
        for day in day_order:
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
        
        display_str = ', '.join(display_parts) if display_parts else 'Custom Schedule'
        print(f"[HOURS] Extracted: {display_str}")
        
        return {
            'is_24_7': False,
            'display': display_str,
            'source': source,
            'ranges': day_ranges
        }
    
    def _extract_answering_rules_aggressive(self, ext_id):
        """
        Extract ALL answering rules with complete details.
        No shortcuts, no assumptions.
        """
        print(f"[RULES] Extracting answering rules for extension {ext_id}...")
        
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule',
            method='GET',
            raise_error=False
        )
        
        rules = {
            'business_hours': None,
            'after_hours': None,
            'custom': []
        }
        
        if not isinstance(resp, dict):
            print(f"[RULES] WARNING: No answering rules data")
            return rules
        
        records = safe_list(resp, 'records')
        print(f"[RULES] Found {len(records)} answering rules")
        
        for rule in records:
            if not isinstance(rule, dict):
                continue
            
            # Only process enabled rules
            if not rule.get('enabled', False):
                print(f"[RULES] Skipping disabled rule: {rule.get('name')}")
                continue
            
            rule_type = rule.get('type')
            rule_name = rule.get('name', 'Unnamed')
            
            print(f"[RULES] Processing {rule_type} rule: {rule_name}")
            
            rule_data = {
                'id': rule.get('id'),
                'name': rule_name,
                'type': rule_type,
                'enabled': True,
                'raw': rule
            }
            
            if rule_type == 'BusinessHours':
                rules['business_hours'] = rule_data
            elif rule_type == 'AfterHours':
                rules['after_hours'] = rule_data
            elif rule_type == 'Custom':
                # Extract custom rule conditions
                rule_data['conditions'] = self._extract_custom_conditions(rule)
                rules['custom'].append(rule_data)
        
        print(f"[RULES] Summary - BH: {bool(rules['business_hours'])}, AH: {bool(rules['after_hours'])}, Custom: {len(rules['custom'])}")
        return rules
    
    def _extract_custom_conditions(self, rule):
        """Extract exact custom rule conditions"""
        conditions = {
            'caller_ids': [],
            'called_numbers': [],
            'schedule': None,
            'description': []
        }
        
        # Caller IDs
        for caller in safe_list(rule, 'callers'):
            if isinstance(caller, dict):
                caller_id = caller.get('callerId') or caller.get('name', '')
                if caller_id:
                    conditions['caller_ids'].append(caller_id)
                    conditions['description'].append(f"Caller ID matches {caller_id}")
        
        # Called numbers
        for called in safe_list(rule, 'calledNumbers'):
            if isinstance(called, dict):
                phone = called.get('phoneNumber', '')
                if phone:
                    conditions['called_numbers'].append(phone)
                    conditions['description'].append(f"Called number is {phone}")
        
        # Schedule
        schedule = safe_dict(rule, 'schedule')
        if schedule:
            schedule_ref = schedule.get('ref')
            if schedule_ref:
                conditions['schedule'] = schedule_ref
                conditions['description'].append(f"Schedule: {schedule_ref}")
        
        return conditions
    
    def _extract_queue_config_aggressive(self, ext_id, bh_rule):
        """
        Extract REAL queue configuration from BOTH APIs.
        NO DEFAULTS. If not configured, mark as None.
        """
        print(f"[QUEUE] Extracting queue configuration for extension {ext_id}...")
        
        # Call queue info API
        queue_api = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/call-queue-info',
            method='GET',
            raise_error=False
        )
        
        if not isinstance(queue_api, dict):
            queue_api = {}
            print(f"[QUEUE] WARNING: No call-queue-info data")
        
        # Get queue settings from business hours rule
        queue_settings = {}
        if bh_rule and isinstance(bh_rule, dict):
            queue_settings = safe_dict(bh_rule.get('raw', {}), 'queue')
        
        config = {}
        
        # Transfer mode - CRITICAL - use actual value, no default
        transfer_mode = queue_api.get('transferMode') or queue_settings.get('transferMode')
        if transfer_mode:
            config['transfer_mode'] = transfer_mode
            print(f"[QUEUE] Transfer mode: {transfer_mode}")
        else:
            config['transfer_mode'] = None
            print(f"[QUEUE] WARNING: No transfer mode found!")
        
        # Agent timeout - only if mode is NOT simultaneous
        agent_timeout = queue_api.get('agentTimeout') or queue_settings.get('agentTimeout')
        if agent_timeout and int(agent_timeout) > 0:
            if transfer_mode and transfer_mode.lower() != 'simultaneous':
                config['agent_timeout'] = int(agent_timeout)
                print(f"[QUEUE] Agent timeout: {agent_timeout}s (mode: {transfer_mode})")
            else:
                config['agent_timeout'] = None
                print(f"[QUEUE] Agent timeout exists ({agent_timeout}s) but mode is Simultaneous - not applicable")
        else:
            config['agent_timeout'] = None
        
        # Wrap-up time
        wrap_up = queue_api.get('wrapUpTime') or queue_settings.get('wrapUpTime')
        if wrap_up and int(wrap_up) > 0:
            config['wrap_up_time'] = int(wrap_up)
            print(f"[QUEUE] Wrap-up time: {wrap_up}s")
        else:
            config['wrap_up_time'] = None
            print(f"[QUEUE] No wrap-up time configured")
        
        # Hold time
        hold_time = queue_settings.get('holdTime') or queue_api.get('holdTime')
        if hold_time and int(hold_time) > 0:
            config['hold_time'] = int(hold_time)
            print(f"[QUEUE] Hold time: {hold_time}s")
        else:
            config['hold_time'] = None
            print(f"[QUEUE] No hold time configured")
        
        # Max callers
        max_callers = queue_settings.get('maxCallers') or queue_api.get('maxCallers')
        if max_callers and int(max_callers) > 0:
            config['max_callers'] = int(max_callers)
            print(f"[QUEUE] Max callers: {max_callers}")
        else:
            config['max_callers'] = None
            print(f"[QUEUE] No max callers configured")
        
        # Interrupt period
        interrupt = queue_api.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod')
        if interrupt and int(interrupt) > 0:
            config['interrupt_period'] = int(interrupt)
            print(f"[QUEUE] Interrupt period: {interrupt}s")
        else:
            config['interrupt_period'] = None
        
        # Max concurrent calls per agent
        max_concurrent = queue_settings.get('maxCallersPerAgent') or queue_api.get('maxCallersPerAgent')
        if max_concurrent:
            config['max_concurrent_per_agent'] = int(max_concurrent)
            print(f"[QUEUE] Max concurrent per agent: {max_concurrent}")
        else:
            config['max_concurrent_per_agent'] = None
        
        # Extract agents
        config['agents'] = []
        agents = queue_api.get('fixedOrderAgents', []) or queue_api.get('agents', [])
        
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            
            ext_obj = safe_dict(agent, 'extension')
            agent_id = str(ext_obj.get('id', ''))
            
            if agent_id and agent_id in self.ext_directory:
                agent_info = self.ext_directory[agent_id]
                config['agents'].append({
                    'id': agent_id,
                    'name': agent_info['name'],
                    'number': agent_info['number'],
                    'email': agent_info.get('email'),
                    'first_name': agent_info.get('first_name'),
                    'last_name': agent_info.get('last_name')
                })
        
        print(f"[QUEUE] Found {len(config['agents'])} agents")
        
        # Extract overflow actions
        config['overflow_actions'] = {}
        
        overflow_keys = [
            ('noAnswerAction', 'no_agents'),
            ('holdTimeExpirationAction', 'max_wait'),
            ('maxCallersAction', 'max_callers')
        ]
        
        for key, label in overflow_keys:
            action = queue_settings.get(key)
            if action:
                target_id, target_name, target_type = self._extract_routing_target(action)
                config['overflow_actions'][label] = {
                    'target_id': target_id,
                    'target_name': target_name,
                    'target_type': target_type
                }
                print(f"[QUEUE] Overflow {label}: {target_name}")
        
        # Check for greetings
        config['greetings'] = {
            'has_introductory': False,
            'has_connecting': False,
            'has_hold_music': False,
            'has_interrupt': False
        }
        
        if bh_rule and isinstance(bh_rule, dict):
            for greeting in safe_list(bh_rule.get('raw', {}), 'greetings'):
                if isinstance(greeting, dict):
                    g_type = greeting.get('type')
                    if g_type == 'Introductory':
                        config['greetings']['has_introductory'] = True
                    elif g_type == 'ConnectingAudio':
                        config['greetings']['has_connecting'] = True
                    elif g_type == 'HoldMusic':
                        config['greetings']['has_hold_music'] = True
                    elif g_type == 'InterruptPrompt':
                        config['greetings']['has_interrupt'] = True
        
        print(f"[QUEUE] Greetings - Intro: {config['greetings']['has_introductory']}, Hold: {config['greetings']['has_hold_music']}")
        
        return config
    
    def _extract_routing_target(self, action_obj):
        """Extract routing target from action object"""
        if not isinstance(action_obj, dict):
            return None, 'Unknown Destination', 'unknown'
        
        # Extension transfer
        ext = safe_dict(action_obj, 'extension')
        if not ext:
            transfer = safe_dict(action_obj, 'transfer')
            ext = safe_dict(transfer, 'extension')
        
        if ext and ext.get('id'):
            target_id = str(ext.get('id'))
            if target_id in self.ext_directory:
                info = self.ext_directory[target_id]
                return target_id, f"{info['name']} (Ext {info['number']})", info['type']
            return target_id, f"Extension {target_id}", 'extension'
        
        # Voicemail
        voicemail = safe_dict(action_obj, 'voicemail')
        recipient = safe_dict(voicemail, 'recipient')
        if recipient and recipient.get('id'):
            target_id = str(recipient.get('id'))
            if target_id in self.ext_directory:
                info = self.ext_directory[target_id]
                return target_id, f"Voicemail of {info['name']}", 'voicemail'
            return target_id, f"Voicemail", 'voicemail'
        
        # External forwarding
        forwarding = safe_dict(action_obj, 'unconditionalForwarding')
        if forwarding and forwarding.get('phoneNumber'):
            phone = forwarding.get('phoneNumber')
            return None, f"External: {self._format_phone_number(phone)}", 'external'
        
        return None, 'Configured Destination', 'unknown'
    
    def process_all_flows(self):
        """Main processing loop"""
        print(f"\n[PROCESS] Starting call flow processing...")
        
        while self.processing_queue:
            current = self.processing_queue.pop(0)
            
            ext_id = current['id']
            ext_name = current['name']
            ext_number = current['number']
            ext_type = current['type']
            path = current['path']
            depth = current['depth']
            context = current['context']
            
            print(f"\n[PROCESS] Processing: {ext_name} (Ext {ext_number}, Type: {ext_type}, Depth: {depth})")
            
            # Prevent infinite loops
            if depth > 10:
                print(f"[PROCESS] Skipping - max depth reached")
                continue
            
            # Track processing
            process_key = f"{ext_id}:{context}"
            if process_key in self.processed_extensions:
                print(f"[PROCESS] Skipping - already processed in this context")
                continue
            self.processed_extensions.add(process_key)
            
            # Build path display
            path_display = ' → '.join(path + [ext_name]) if path else ext_name
            path_prefix = f"[{path_display}] " if path else ""
            
            # Generate tests based on type
            if ext_type == 'Department':
                self._generate_queue_tests(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
            elif ext_type == 'IvrMenu':
                self._generate_ivr_tests(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
            else:
                self._generate_basic_tests(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
        
        # Global tests
        self._add_global_tests()
        
        print(f"\n[COMPLETE] Generated {len(self.test_cases)} test cases")
        return self.test_cases
    
    def _generate_queue_tests(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate queue tests with REAL data"""
        
        print(f"\n[GEN-QUEUE] Generating tests for queue: {ext_name}")
        
        # Extract phone numbers
        numbers = self._extract_phone_numbers_aggressive(ext_id)
        
        # Extract business hours
        hours = self._extract_business_hours_aggressive(ext_id)
        
        # Extract answering rules
        rules = self._extract_answering_rules_aggressive(ext_id)
        
        # Extract queue config
        queue_config = self._extract_queue_config_aggressive(ext_id, rules['business_hours'])
        
        # === INTEGRATION TESTS ===
        if depth == 0:
            self.add_test(
                f"{path_prefix}Integration",
                "Internal Extension Dialing",
                f"Using a desk phone or RingCentral app logged into the company account, dial extension {ext_number}.",
                f"Call connects to {ext_name} with clear two-way audio. No SIP errors or dead air."
            )
            
            # PSTN tests with ACTUAL phone numbers
            for num in numbers:
                if num['usage_type'] == 'DirectNumber':
                    self.add_test(
                        f"{path_prefix}Integration - PSTN",
                        f"External Direct Number: {num['formatted']}",
                        f"From your personal mobile phone (NOT connected to company network), dial {num['formatted']}",
                        f"Call routes through PSTN network and connects to {ext_name}. Caller ID shows as your mobile number. Clear two-way audio."
                    )
                elif num['usage_type'] == 'MainCompanyNumber':
                    self.add_test(
                        f"{path_prefix}Integration - Main Number",
                        f"Main Company Number: {num['formatted']}",
                        f"From external phone, dial main company number {num['formatted']}. Follow IVR prompts to reach {ext_name}.",
                        f"Successfully reaches {ext_name} after IVR navigation."
                    )
        
        # === BUSINESS HOURS ===
        if hours['has_custom']:
            if hours['ranges']:
                first_day = next(iter(hours['ranges'].keys()))
                first_range = hours['ranges'][first_day][0] if hours['ranges'][first_day] else None
                
                if first_range:
                    self.add_test(
                        f"{path_prefix}Time Routing",
                        "Business Hours - Open",
                        f"Place test call on {first_day} at {first_range['from']} (within hours: {hours['display']}).",
                        f"Call follows Business Hours routing configured for {ext_name}."
                    )
                    
                    self.add_test(
                        f"{path_prefix}Time Routing",
                        "Business Hours - Closed",
                        f"Place test call on Sunday at 11:00 PM or {first_day} at 11:00 PM (outside hours: {hours['display']}).",
                        f"Call follows After Hours routing for {ext_name}."
                    )
        
        # === AFTER HOURS RULE ===
        if rules['after_hours']:
            target_id, target_name, target_type = self._extract_routing_target(rules['after_hours']['raw'])
            
            self.add_test(
                f"{path_prefix}After Hours",
                "After Hours Routing",
                f"Place call outside business hours.",
                f"After Hours rule activates. Call routes to: {target_name}"
            )
            
            # Recursively process
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
                        'context': 'After Hours'
                    })
        
        # === CUSTOM RULES ===
        for custom_rule in rules['custom']:
            conditions = custom_rule['conditions']
            
            if conditions['description']:
                target_id, target_name, target_type = self._extract_routing_target(custom_rule['raw'])
                
                condition_text = ' AND '.join(conditions['description'])
                
                self.add_test(
                    f"{path_prefix}Custom Rule",
                    f"{custom_rule['name']}",
                    f"Trigger condition: {condition_text}",
                    f"Custom rule activates and routes to: {target_name}"
                )
                
                # Recursively process
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
                            'context': f"Custom Rule '{custom_rule['name']}'"
                        })
        
        # === QUEUE EXPERIENCE ===
        if queue_config['greetings']['has_introductory']:
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                "Introductory Greeting",
                f"Place call to {ext_name}.",
                "Introductory greeting plays completely before agent ringing or hold music begins."
            )
        
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Hold Music / Connecting Audio",
            f"Place call to {ext_name} and wait in queue.",
            "Hold music or comfort message plays clearly without distortion."
        )
        
        if queue_config['interrupt_period']:
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                f"Periodic Announcements ({queue_config['interrupt_period']}s)",
                f"Remain on hold for at least {queue_config['interrupt_period'] + 10} seconds.",
                f"Every {queue_config['interrupt_period']} seconds, music pauses, announcement plays, music resumes."
            )
        
        # === AGENT TESTS ===
        if queue_config['agents']:
            first_agent = queue_config['agents'][0]
            agent_display = f"{first_agent['name']} (Ext {first_agent['number']})"
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Agent Opt-In",
                f"Agent {agent_display} enables 'Accept Queue Calls' for {ext_name}. Place test call.",
                f"Agent {agent_display}'s device rings. Caller ID shows queue name: '{ext_name}'."
            )
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Agent Opt-Out / DND",
                f"Agent {agent_display} disables 'Accept Queue Calls' OR sets DND. Place test call.",
                f"Agent {agent_display} does NOT ring. Call routes to next available agent."
            )
            
            if queue_config['wrap_up_time']:
                self.add_test(
                    f"{path_prefix}Queue - Agent Tests",
                    f"After-Call Work ({queue_config['wrap_up_time']}s)",
                    f"Agent {agent_display} completes call. Immediately place second call.",
                    f"Agent enters Wrap-Up for {queue_config['wrap_up_time']}s. Does NOT ring during this period."
                )
        
        # === DISTRIBUTION ===
        if queue_config['transfer_mode']:
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                f"Distribution Mode: {queue_config['transfer_mode']}",
                f"Ensure 2+ agents available. Place test call.",
                f"Call distributes per {queue_config['transfer_mode']} logic."
            )
            
            # Only test ring timeout if applicable
            if queue_config['agent_timeout']:
                self.add_test(
                    f"{path_prefix}Queue - Distribution",
                    f"Agent Ring Timeout ({queue_config['agent_timeout']}s)",
                    f"First agent ignores call for {queue_config['agent_timeout']}s.",
                    f"After {queue_config['agent_timeout']}s, call hunts to next agent."
                )
        
        # === CALL HANDLING ===
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Hold Function",
            "Agent answers call and clicks Hold.",
            "Caller hears hold music. Agent can retrieve call."
        )
        
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer",
            "Agent initiates warm transfer, consults, completes.",
            "Transfer successful with two-way audio."
        )
        
        # === OVERFLOWS ===
        if 'no_agents' in queue_config['overflow_actions']:
            overflow = queue_config['overflow_actions']['no_agents']
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                "No Agents Available",
                f"All agents logged out or DND. Place call.",
                f"Call bypasses queue. Routes to: {overflow['target_name']}"
            )
            
            # Recursive
            if overflow['target_id'] and overflow['target_id'] in self.ext_directory:
                dest = self.ext_directory[overflow['target_id']]
                if dest['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': overflow['target_id'],
                        'name': dest['name'],
                        'number': dest['number'],
                        'type': dest['type'],
                        'path': path + [ext_name],
                        'depth': depth + 1,
                        'context': 'No Agents Overflow'
                    })
        
        if queue_config['hold_time'] and 'max_wait' in queue_config['overflow_actions']:
            overflow = queue_config['overflow_actions']['max_wait']
            hold_time = queue_config['hold_time']
            
            minutes = hold_time // 60
            seconds = hold_time % 60
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Max Wait Time ({time_str})",
                f"Remain on hold for {hold_time} seconds.",
                f"Call removed from queue. Routes to: {overflow['target_name']}"
            )
            
            # Recursive
            if overflow['target_id'] and overflow['target_id'] in self.ext_directory:
                dest = self.ext_directory[overflow['target_id']]
                if dest['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': overflow['target_id'],
                        'name': dest['name'],
                        'number': dest['number'],
                        'type': dest['type'],
                        'path': path + [ext_name],
                        'depth': depth + 1,
                        'context': f'Max Wait ({time_str}) Overflow'
                    })
    
    def _generate_ivr_tests(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate IVR tests"""
        # Simplified for now - can expand later
        pass
    
    def _generate_basic_tests(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate basic tests"""
        pass
    
    def _add_global_tests(self):
        """Add global tests"""
        self.add_test(
            "Global Validation",
            "Call Logs",
            "Check Admin Portal > Call Logs.",
            "All test calls logged with correct details."
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
    Generate UAT cases with REAL data.
    May take 1-2 minutes for complex flows - that's OK.
    """
    analyzer = TrulyForensicAnalyzer(
        extension_id,
        extension_name,
        extension_number,
        extension_type
    )
    
    return analyzer.process_all_flows()
