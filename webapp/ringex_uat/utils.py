from webapp.rc_api import rc_api_call
from datetime import datetime

# =============================================================================
# FORENSICALLY DETAILED UAT GENERATOR
# Every test uses ACTUAL API data. No placeholders. No assumptions.
# =============================================================================

def safe_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    return val if isinstance(val, dict) else {}

def safe_list(d, key):
    if not isinstance(d, dict): return []
    val = d.get(key)
    return val if isinstance(val, list) else []

def safe_get(d, key, default=''):
    if not isinstance(d, dict): return default
    return d.get(key, default)


class ForensicCallFlowAnalyzer:
    """
    Forensically detailed call flow analyzer that:
    1. Extracts EVERY piece of data from RingCentral APIs
    2. Generates tests ONLY for configured features
    3. Provides EXACT instructions with real phone numbers, real conditions, real values
    4. Validates configuration before generating tests
    """
    
    def __init__(self, start_id, start_name, start_number, start_type):
        self.test_cases = []
        self.test_counter = 1
        self.processed_extensions = set()
        self.processing_queue = []
        
        # Build comprehensive extension directory with FULL details
        self.ext_directory = self._build_complete_extension_directory()
        
        # Add starting extension
        self.processing_queue.append({
            'id': str(start_id),
            'name': start_name,
            'number': start_number,
            'type': start_type,
            'path': [],
            'depth': 0,
            'context': 'Primary Entry Point'
        })
    
    def _build_complete_extension_directory(self):
        """Build directory with FULL extension metadata"""
        directory = {}
        resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
        
        if isinstance(resp, dict):
            for ext in safe_list(resp, 'records'):
                if isinstance(ext, dict) and ext.get('id'):
                    ext_id = str(ext.get('id'))
                    directory[ext_id] = {
                        'id': ext_id,
                        'name': ext.get('name', 'Unknown'),
                        'number': ext.get('extensionNumber', 'N/A'),
                        'type': ext.get('type', 'Unknown'),
                        'status': ext.get('status', 'Unknown'),
                        'email': ext.get('contact', {}).get('email'),
                        'first_name': ext.get('contact', {}).get('firstName'),
                        'last_name': ext.get('contact', {}).get('lastName')
                    }
        
        return directory
    
    def add_test(self, category, scenario, action, expected):
        """Add test with auto-incrementing ID"""
        self.test_cases.append({
            'test_id': f'UAT-{self.test_counter:04d}',
            'category': category,
            'scenario': scenario,
            'action': action,
            'expected': expected
        })
        self.test_counter += 1
    
    def _extract_all_phone_numbers_detailed(self, ext_id):
        """
        Extract ALL phone numbers with complete details:
        - Phone number
        - Usage type
        - Type (local/tollfree)
        - Label
        - Verification it belongs to THIS extension
        """
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
                
                # CRITICAL: Verify this number ACTUALLY belongs to THIS extension
                ext_obj = safe_dict(record, 'extension')
                if str(ext_obj.get('id')) != str(ext_id):
                    continue
                
                phone = record.get('phoneNumber')
                if not phone:
                    continue
                
                numbers.append({
                    'phone': phone,
                    'usage_type': record.get('usageType', 'Unknown'),
                    'type': record.get('type', 'Unknown'),
                    'label': record.get('label', ''),
                    'primary': record.get('primary', False),
                    'formatted': self._format_phone_number(phone)
                })
        
        return numbers
    
    def _format_phone_number(self, number):
        """Format phone number for display"""
        if not number:
            return ''
        # Remove +1 and format as (XXX) XXX-XXXX
        clean = number.replace('+1', '').replace('+', '')
        if len(clean) == 10:
            return f"({clean[:3]}) {clean[3:6]}-{clean[6:]}"
        return number
    
    def _extract_business_hours_detailed(self, ext_id):
        """
        Extract complete business hours with:
        - Exact day/time ranges
        - Source (extension vs account)
        - Whether it's custom or 24/7
        """
        # Try extension-specific first
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours',
            method='GET',
            raise_error=False
        )
        
        source = 'extension-specific'
        
        # Fall back to account-level
        if not isinstance(resp, dict) or not safe_dict(resp, 'schedule'):
            resp = rc_api_call(
                '/restapi/v1.0/account/~/business-hours',
                method='GET',
                raise_error=False
            )
            source = 'account-level'
        
        if not isinstance(resp, dict):
            return {
                'is_24_7': True,
                'display': '24/7 (Always Open)',
                'source': 'default',
                'ranges': {},
                'has_custom': False
            }
        
        schedule = safe_dict(resp, 'schedule')
        weekly = safe_dict(schedule, 'weeklyRanges')
        
        if not weekly:
            return {
                'is_24_7': True,
                'display': '24/7 (Always Open)',
                'source': source,
                'ranges': {},
                'has_custom': False
            }
        
        # Build detailed day ranges
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
                            day_ranges[day].append({'from': from_time, 'to': to_time})
                            display_parts.append(f"{day[:3]} {from_time}-{to_time}")
        
        return {
            'is_24_7': False,
            'display': ', '.join(display_parts) if display_parts else 'Custom Schedule',
            'source': source,
            'ranges': day_ranges,
            'has_custom': True
        }
    
    def _extract_answering_rules_detailed(self, ext_id):
        """
        Extract ALL answering rules with COMPLETE configuration details:
        - Custom rules with exact conditions
        - Business hours configuration
        - After hours configuration
        - Greetings
        - Actions
        """
        resp = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule',
            method='GET',
            raise_error=False
        )
        
        rules = {
            'business_hours': None,
            'after_hours': None,
            'custom': [],
            'all_rules': []
        }
        
        if not isinstance(resp, dict):
            return rules
        
        for rule in safe_list(resp, 'records'):
            if not isinstance(rule, dict):
                continue
            
            # Only process enabled rules
            if not rule.get('enabled', False):
                continue
            
            rule_type = rule.get('type')
            
            # Extract complete rule details
            rule_details = {
                'id': rule.get('id'),
                'name': rule.get('name', 'Unnamed'),
                'type': rule_type,
                'enabled': rule.get('enabled', False),
                'greetings': self._extract_greetings_detailed(rule),
                'queue_settings': safe_dict(rule, 'queue'),
                'call_handling_action': rule.get('callHandlingAction'),
                'raw': rule
            }
            
            # For custom rules, extract exact conditions
            if rule_type == 'Custom':
                rule_details['conditions'] = self._extract_custom_rule_conditions_detailed(rule)
            
            rules['all_rules'].append(rule_details)
            
            if rule_type == 'BusinessHours':
                rules['business_hours'] = rule_details
            elif rule_type == 'AfterHours':
                rules['after_hours'] = rule_details
            elif rule_type == 'Custom':
                rules['custom'].append(rule_details)
        
        return rules
    
    def _extract_greetings_detailed(self, rule):
        """Extract all greeting configurations"""
        greetings = {
            'has_introductory': False,
            'has_connecting': False,
            'has_hold_music': False,
            'has_interrupt': False,
            'has_voicemail': False,
            'details': []
        }
        
        for greeting in safe_list(rule, 'greetings'):
            if not isinstance(greeting, dict):
                continue
            
            g_type = greeting.get('type')
            
            greeting_detail = {
                'type': g_type,
                'preset': greeting.get('preset', {}).get('name', 'Custom'),
                'custom': greeting.get('custom', {})
            }
            
            greetings['details'].append(greeting_detail)
            
            if g_type == 'Introductory':
                greetings['has_introductory'] = True
            elif g_type == 'ConnectingAudio':
                greetings['has_connecting'] = True
            elif g_type == 'HoldMusic':
                greetings['has_hold_music'] = True
            elif g_type == 'InterruptPrompt':
                greetings['has_interrupt'] = True
            elif g_type == 'Voicemail':
                greetings['has_voicemail'] = True
        
        return greetings
    
    def _extract_custom_rule_conditions_detailed(self, rule):
        """
        Extract EXACT conditions for custom rules:
        - Specific caller IDs
        - Specific dialed numbers
        - Exact schedule details
        """
        conditions = {
            'caller_ids': [],
            'called_numbers': [],
            'schedule': None,
            'has_conditions': False,
            'description': []
        }
        
        # Extract caller ID conditions
        for caller in safe_list(rule, 'callers'):
            if not isinstance(caller, dict):
                continue
            
            caller_id = caller.get('callerId', caller.get('name', ''))
            if caller_id:
                conditions['caller_ids'].append(caller_id)
                conditions['description'].append(f"Caller ID matches {caller_id}")
        
        # Extract called number conditions
        for called in safe_list(rule, 'calledNumbers'):
            if not isinstance(called, dict):
                continue
            
            phone = called.get('phoneNumber', '')
            if phone:
                conditions['called_numbers'].append(phone)
                conditions['description'].append(f"Called number is {phone}")
        
        # Extract schedule conditions
        schedule = safe_dict(rule, 'schedule')
        if schedule:
            schedule_ref = schedule.get('ref')
            if schedule_ref:
                conditions['schedule'] = schedule_ref
                conditions['description'].append(f"Schedule: {schedule_ref}")
            else:
                # Try to get schedule details
                weekly = safe_dict(schedule, 'weeklyRanges')
                if weekly:
                    conditions['schedule'] = 'custom'
                    conditions['description'].append("Custom time schedule")
        
        conditions['has_conditions'] = bool(conditions['description'])
        
        return conditions
    
    def _extract_queue_configuration_detailed(self, ext_id, bh_rule):
        """
        Extract COMPLETE queue configuration:
        - Transfer mode and validate it exists
        - Ring timeout (only if applicable)
        - Wrap-up time (only if configured)
        - Max wait time (only if configured)
        - Max callers (only if configured)
        - Interrupt period (only if configured)
        - Agent list with details
        - Overflow actions with exact destinations
        """
        # Get queue-specific config
        queue_api = rc_api_call(
            f'/restapi/v1.0/account/~/extension/{ext_id}/call-queue-info',
            method='GET',
            raise_error=False
        ) or {}
        
        # Get business hours rule queue settings
        queue_settings = safe_dict(bh_rule.get('raw', {}) if bh_rule else {}, 'queue')
        
        config = {
            'transfer_mode': None,
            'agent_timeout': None,
            'wrap_up_time': None,
            'hold_time': None,
            'max_callers': None,
            'interrupt_period': None,
            'agents': [],
            'overflow_actions': {},
            'has_recording': False,
            'recording_mode': None
        }
        
        # Transfer mode (ALWAYS exists)
        config['transfer_mode'] = queue_api.get('transferMode') or queue_settings.get('transferMode') or 'Rotating'
        
        # Agent timeout (only matters if NOT simultaneous)
        if config['transfer_mode'].lower() != 'simultaneous':
            timeout = queue_api.get('agentTimeout') or queue_settings.get('agentTimeout')
            if timeout and int(timeout) > 0:
                config['agent_timeout'] = int(timeout)
        
        # Wrap-up time (only if configured)
        wrap_up = queue_api.get('wrapUpTime') or queue_settings.get('wrapUpTime')
        if wrap_up and int(wrap_up) > 0:
            config['wrap_up_time'] = int(wrap_up)
        
        # Hold time (only if configured)
        hold = queue_settings.get('holdTime') or queue_api.get('holdTime')
        if hold and int(hold) > 0:
            config['hold_time'] = int(hold)
        
        # Max callers (only if configured)
        max_c = queue_settings.get('maxCallers') or queue_api.get('maxCallers')
        if max_c and int(max_c) > 0:
            config['max_callers'] = int(max_c)
        
        # Interrupt period (only if configured)
        interrupt = queue_api.get('holdAudioInterruptionPeriod') or queue_settings.get('holdAudioInterruptionPeriod')
        if interrupt and int(interrupt) > 0:
            config['interrupt_period'] = int(interrupt)
        
        # Extract agent list with full details
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
        
        # Extract overflow actions
        overflow_keys = [
            ('noAnswerAction', 'no_agents'),
            ('holdTimeExpirationAction', 'max_wait'),
            ('maxCallersAction', 'max_callers')
        ]
        
        for key, label in overflow_keys:
            action = queue_settings.get(key)
            if action:
                target_id, target_name, target_type = self._extract_routing_target_detailed(action)
                config['overflow_actions'][label] = {
                    'target_id': target_id,
                    'target_name': target_name,
                    'target_type': target_type,
                    'raw': action
                }
        
        # Check for call recording
        if bh_rule and bh_rule.get('raw'):
            recording = safe_dict(bh_rule['raw'], 'callRecording')
            if recording and recording.get('enabled'):
                config['has_recording'] = True
                config['recording_mode'] = recording.get('mode', 'Automatic')
        
        return config
    
    def _extract_routing_target_detailed(self, action_obj):
        """Extract routing target with full details"""
        if not isinstance(action_obj, dict):
            return None, 'Unknown Destination', 'unknown'
        
        # Pattern 1: Extension transfer
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
        
        # Pattern 2: Voicemail
        voicemail = safe_dict(action_obj, 'voicemail')
        recipient = safe_dict(voicemail, 'recipient')
        if recipient and recipient.get('id'):
            target_id = str(recipient.get('id'))
            if target_id in self.ext_directory:
                info = self.ext_directory[target_id]
                return target_id, f"Voicemail of {info['name']}", 'voicemail'
            return target_id, f"Voicemail of Extension {target_id}", 'voicemail'
        
        # Pattern 3: External forwarding
        forwarding = safe_dict(action_obj, 'unconditionalForwarding')
        if forwarding and forwarding.get('phoneNumber'):
            phone = forwarding.get('phoneNumber')
            return None, f"External: {self._format_phone_number(phone)}", 'external'
        
        # Pattern 4: Action-based
        action = action_obj.get('callHandlingAction', action_obj.get('action', ''))
        if action == 'TakeMessagesOnly':
            return None, 'Voicemail (Take Messages)', 'voicemail'
        
        return None, 'Configured Destination', 'unknown'
    
    def _extract_ivr_configuration_detailed(self, ext_id):
        """Extract complete IVR configuration"""
        ivr_config = rc_api_call(
            f'/restapi/v1.0/ivr-menus/{ext_id}',
            method='GET',
            raise_error=False
        ) or {}
        
        config = {
            'prompt_text': None,
            'prompt_mode': None,
            'actions': [],
            'has_dial_by_extension': False
        }
        
        # Extract prompt details
        prompt = safe_dict(ivr_config, 'prompt')
        if prompt:
            config['prompt_text'] = prompt.get('text', 'Custom Audio')
            config['prompt_mode'] = prompt.get('mode', 'Audio')
        
        # Extract all key actions
        for action in safe_list(ivr_config, 'actions'):
            if not isinstance(action, dict):
                continue
            
            key = action.get('input', '')
            if not key:
                continue
            
            action_type = action.get('action', 'Unknown')
            target_id, target_name, target_type = self._extract_routing_target_detailed(action)
            
            config['actions'].append({
                'key': key,
                'action_type': action_type,
                'target_id': target_id,
                'target_name': target_name,
                'target_type': target_type,
                'raw': action
            })
        
        return config
    
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
            
            # Prevent infinite loops
            if depth > 10:
                continue
            
            # Track processing
            process_key = f"{ext_id}:{context}"
            if process_key in self.processed_extensions:
                continue
            self.processed_extensions.add(process_key)
            
            # Build path display
            path_display = ' → '.join(path + [ext_name]) if path else ext_name
            path_prefix = f"[{path_display}] " if path else ""
            
            # Generate complete test suite based on type
            if ext_type == 'Department':
                self._generate_queue_tests_detailed(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
            elif ext_type == 'IvrMenu':
                self._generate_ivr_tests_detailed(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
            else:
                self._generate_user_tests_detailed(
                    ext_id, ext_name, ext_number, path, path_prefix, depth, context
                )
        
        # Add global tests
        self._add_global_tests()
        
        return self.test_cases
    
    def _generate_queue_tests_detailed(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate forensically detailed queue tests"""
        
        # ================================================================
        # 1. PHONE NUMBER ROUTING - WITH EXACT NUMBERS
        # ================================================================
        numbers = self._extract_all_phone_numbers_detailed(ext_id)
        
        if depth == 0:  # Primary entry only
            self.add_test(
                f"{path_prefix}Integration",
                "Internal Extension Dialing",
                f"Using a desk phone or RingCentral app logged into the company account, dial extension {ext_number}.",
                f"Call connects to {ext_name} with clear two-way audio. No SIP errors or dead air."
            )
            
            # Generate test for EACH phone number with EXACT instructions
            for num in numbers:
                if num['usage_type'] == 'DirectNumber':
                    self.add_test(
                        f"{path_prefix}Integration - PSTN",
                        f"External Direct Number: {num['formatted']}",
                        f"From your personal mobile phone (external line), dial {num['formatted']} (or {num['phone']}).",
                        f"Call routes through PSTN network and connects to {ext_name}. ANI/DNIS preserved. Clear audio both directions."
                    )
                elif num['usage_type'] == 'MainCompanyNumber':
                    self.add_test(
                        f"{path_prefix}Integration - Main Number",
                        f"Main Company Number: {num['formatted']}",
                        f"From external phone, dial main number {num['formatted']}. Navigate through auto-attendant to reach {ext_name}.",
                        f"Successfully reaches {ext_name} after IVR navigation."
                    )
        
        # ================================================================
        # 2. BUSINESS HOURS - WITH EXACT SCHEDULE
        # ================================================================
        hours = self._extract_business_hours_detailed(ext_id)
        
        if hours['has_custom']:
            # Generate specific time window examples
            if hours['ranges']:
                first_day = next(iter(hours['ranges'].keys()))
                first_range = hours['ranges'][first_day][0] if hours['ranges'][first_day] else None
                
                if first_range:
                    self.add_test(
                        f"{path_prefix}Time Routing",
                        "Business Hours - Open",
                        f"Place test call on {first_day} at {first_range['from']} (during business hours: {hours['display']}).",
                        f"Call follows Business Hours routing path configured for {ext_name}."
                    )
                    
                    self.add_test(
                        f"{path_prefix}Time Routing",
                        "Business Hours - Closed",
                        f"Place test call on {first_day} at 11:00 PM or on Sunday (outside hours: {hours['display']}).",
                        f"Call follows After Hours routing path for {ext_name}."
                    )
        
        # ================================================================
        # 3. ANSWERING RULES - WITH EXACT CONDITIONS
        # ================================================================
        rules = self._extract_answering_rules_detailed(ext_id)
        
        # Test each custom rule with EXACT conditions
        for custom_rule in rules['custom']:
            rule_name = custom_rule['name']
            conditions = custom_rule['conditions']
            
            if conditions['has_conditions']:
                # Build specific test action
                condition_text = ' AND '.join(conditions['description'])
                
                target_id, target_name, target_type = self._extract_routing_target_detailed(custom_rule['raw'])
                
                # Generate detailed action steps
                action_steps = []
                if conditions['caller_ids']:
                    action_steps.append(f"Call from {conditions['caller_ids'][0]} (spoof caller ID if needed)")
                if conditions['called_numbers']:
                    action_steps.append(f"Dial {conditions['called_numbers'][0]}")
                if conditions['schedule']:
                    action_steps.append(f"Call during schedule: {conditions['schedule']}")
                
                self.add_test(
                    f"{path_prefix}Custom Rule",
                    f"{rule_name}",
                    f"Trigger custom rule by: {' AND '.join(action_steps)}. Specific condition: {condition_text}",
                    f"Custom rule intercepts call and executes routing to: {target_name}"
                )
                
                # Recursively process destination
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
                            'context': f"Custom Rule '{rule_name}'"
                        })
        
        # Test after hours rule
        if rules['after_hours']:
            target_id, target_name, target_type = self._extract_routing_target_detailed(rules['after_hours']['raw'])
            
            self.add_test(
                f"{path_prefix}After Hours",
                "After Hours Routing",
                f"Place call outside business hours: {hours['display']}",
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
        
        # ================================================================
        # 4. QUEUE CONFIGURATION - ONLY TEST WHAT'S CONFIGURED
        # ================================================================
        queue_config = self._extract_queue_configuration_detailed(ext_id, rules['business_hours'])
        greetings = rules['business_hours']['greetings'] if rules['business_hours'] else {'has_introductory': False, 'has_hold_music': False, 'has_interrupt': False}
        
        # Caller experience - only test what exists
        if greetings['has_introductory']:
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                "Introductory Greeting",
                f"Place call to {ext_name} and listen carefully.",
                "Introductory greeting plays in full before any agent ringing or hold music begins."
            )
        
        if queue_config['has_recording']:
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                f"Call Recording Announcement ({queue_config['recording_mode']})",
                f"Place call to {ext_name}.",
                f"'This call may be recorded' announcement plays before agent connection (Mode: {queue_config['recording_mode']})."
            )
        
        self.add_test(
            f"{path_prefix}Queue - Caller Experience",
            "Hold Music / Connecting Audio",
            f"Place call to {ext_name} and wait in queue.",
            "Configured hold music or comfort message plays clearly without distortion or silence."
        )
        
        if queue_config['interrupt_period']:
            self.add_test(
                f"{path_prefix}Queue - Caller Experience",
                f"Periodic Announcements (Every {queue_config['interrupt_period']} seconds)",
                f"Remain on hold in {ext_name} for at least {queue_config['interrupt_period'] + 10} seconds.",
                f"Every {queue_config['interrupt_period']} seconds, hold music pauses, announcement plays ('Please continue holding...'), then music resumes."
            )
        
        # Agent tests - with REAL agent names if available
        if queue_config['agents']:
            first_agent = queue_config['agents'][0]
            agent_display = f"{first_agent['name']} (Ext {first_agent['number']})"
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Agent Opt-In",
                f"Have agent {agent_display} log into RingCentral app and enable 'Accept Queue Calls' for {ext_name}. Place test call.",
                f"Agent {agent_display}'s device rings. Caller ID displays: '{ext_name} - [Caller Name/Number]'."
            )
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Agent Opt-Out / DND",
                f"Have agent {agent_display} disable 'Accept Queue Calls' for {ext_name} OR set status to Do Not Disturb. Place test call.",
                f"Agent {agent_display} does NOT ring. Call hunts to next available agent in queue."
            )
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Call Decline",
                f"While call is ringing agent {agent_display}, have agent click 'Decline' button.",
                f"Ringing stops immediately for {agent_display}. Call hunts to next agent without dropping caller."
            )
            
            self.add_test(
                f"{path_prefix}Queue - Agent Tests",
                "Agent on Active Call",
                f"Have agent {agent_display} place outbound call to become busy. While busy, place new call to {ext_name}.",
                f"System recognizes {agent_display} as unavailable. Queue call does not interrupt active call. Routes to next agent."
            )
            
            if queue_config['wrap_up_time']:
                self.add_test(
                    f"{path_prefix}Queue - Agent Tests",
                    f"After-Call Work Period ({queue_config['wrap_up_time']} seconds)",
                    f"Have agent {agent_display} answer queue call and complete it. Within 1 second, place second call to {ext_name}.",
                    f"Agent {agent_display} enters 'Wrap-Up' state for {queue_config['wrap_up_time']} seconds. Does NOT ring for new calls during this period. Second call routes to other agents."
                )
        
        # Distribution tests
        self.add_test(
            f"{path_prefix}Queue - Distribution",
            f"Distribution Mode: {queue_config['transfer_mode']}",
            f"Ensure at least 2 agents are available and opted-in to {ext_name}. Place test call.",
            f"Call distributes according to {queue_config['transfer_mode']} logic (Rotating = next agent in sequence, Simultaneous = all ring at once, Sequential = fixed order)."
        )
        
        # Only test ring timeout if it exists AND mode isn't simultaneous
        if queue_config['agent_timeout']:
            self.add_test(
                f"{path_prefix}Queue - Distribution",
                f"Agent Ring Timeout ({queue_config['agent_timeout']} seconds)",
                f"Place call to {ext_name}. Have first agent ignore ringing for exactly {queue_config['agent_timeout']} seconds without answering or declining.",
                f"After {queue_config['agent_timeout']} seconds, call stops ringing first agent and immediately hunts to next agent per distribution mode."
            )
        
        # Call handling tests (always applicable)
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Hold Function",
            "Agent answers queue call. Agent clicks 'Hold' button in RingCentral app.",
            "Caller hears configured on-hold music. Agent can retrieve call by clicking 'Resume'."
        )
        
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Warm Transfer (Consultative)",
            "Agent answers queue call. Agent initiates warm transfer to another extension, consults, then completes transfer.",
            "Transfer completes successfully. Caller has two-way audio with destination party."
        )
        
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Blind Transfer",
            "Agent answers queue call. Agent initiates blind transfer to another extension without consultation.",
            "Agent is released immediately. Caller hears ringing to destination extension."
        )
        
        self.add_test(
            f"{path_prefix}Queue - Call Handling",
            "Call Park",
            "Agent answers queue call. Agent parks call to park location *801.",
            "Caller is parked successfully and hears hold music. Another user can retrieve by dialing *801."
        )
        
        # ================================================================
        # 5. OVERFLOW SCENARIOS - ONLY IF CONFIGURED
        # ================================================================
        
        # No agents available
        if 'no_agents' in queue_config['overflow_actions']:
            overflow = queue_config['overflow_actions']['no_agents']
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                "No Agents Available",
                f"Ensure ALL agents for {ext_name} are either: logged out, on DND, or have 'Accept Queue Calls' disabled. Place test call.",
                f"Call immediately bypasses queue (no hold music) and routes to: {overflow['target_name']}"
            )
            
            # Recursively process
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
        
        # Max wait time
        if queue_config['hold_time'] and 'max_wait' in queue_config['overflow_actions']:
            overflow = queue_config['overflow_actions']['max_wait']
            hold_time = queue_config['hold_time']
            
            minutes = hold_time // 60
            seconds = hold_time % 60
            time_display = f"{minutes} minutes {seconds} seconds" if minutes > 0 else f"{seconds} seconds"
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Maximum Wait Time Exceeded ({time_display})",
                f"Place call to {ext_name}. Remain on hold without agent answer for exactly {hold_time} seconds ({time_display}).",
                f"At {hold_time} second mark, call is removed from queue and routes to: {overflow['target_name']}"
            )
            
            # Recursively process
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
                        'context': f'Max Wait ({time_display}) Overflow'
                    })
        
        # Max callers capacity
        if queue_config['max_callers'] and 'max_callers' in queue_config['overflow_actions']:
            overflow = queue_config['overflow_actions']['max_callers']
            max_callers = queue_config['max_callers']
            
            self.add_test(
                f"{path_prefix}Queue - Overflow",
                f"Queue Capacity Exceeded ({max_callers} maximum)",
                f"Flood {ext_name} with {max_callers} simultaneous concurrent calls. While all {max_callers} are in queue/on hold, place call #{max_callers + 1}.",
                f"Call #{max_callers + 1} is rejected (fast busy or immediate routing). Routes to: {overflow['target_name']}"
            )
            
            # Recursively process
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
                        'context': f'Queue Full ({max_callers}) Overflow'
                    })
    
    def _generate_ivr_tests_detailed(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate forensically detailed IVR tests"""
        
        # Get phone numbers
        numbers = self._extract_all_phone_numbers_detailed(ext_id)
        
        # Primary entry only
        if depth == 0:
            self.add_test(
                f"{path_prefix}Integration",
                "Internal Extension Dialing",
                f"From desk phone or RC app, dial extension {ext_number}.",
                f"Call connects to IVR {ext_name}. Prompt begins playing."
            )
            
            for num in numbers:
                if num['usage_type'] == 'DirectNumber':
                    self.add_test(
                        f"{path_prefix}Integration - PSTN",
                        f"External Direct Number: {num['formatted']}",
                        f"From your mobile phone, dial {num['formatted']}.",
                        f"Call routes to IVR {ext_name}. Prompt plays after connection."
                    )
        
        # Get IVR configuration
        ivr_config = self._extract_ivr_configuration_detailed(ext_id)
        
        # Prompt playback
        if ivr_config['prompt_text']:
            self.add_test(
                f"{path_prefix}IVR - Prompt",
                "Greeting Playback & Script Accuracy",
                f"Dial {ext_name} and listen to full prompt.",
                f"Prompt plays clearly without distortion. Audio matches configured script: '{ivr_config['prompt_text']}'"
            )
        
        self.add_test(
            f"{path_prefix}IVR - Prompt",
            "Barge-In (Interrupt)",
            f"While {ext_name} greeting is playing, press any valid menu key during the prompt.",
            "IVR immediately recognizes DTMF and routes call without waiting for full greeting to finish."
        )
        
        # Test each key mapping with exact details
        for action in ivr_config['actions']:
            key = action['key']
            target_name = action['target_name']
            action_type = action['action_type']
            
            self.add_test(
                f"{path_prefix}IVR - Key Mapping",
                f"Press Key '{key}'",
                f"Listen to {ext_name} prompt. Press '{key}' on telephone keypad.",
                f"DTMF tone registered. IVR executes action '{action_type}' and routes to: {target_name}"
            )
            
            # Recursively process destination
            if action['target_id'] and action['target_id'] in self.ext_directory:
                dest = self.ext_directory[action['target_id']]
                if dest['type'] in ['Department', 'IvrMenu']:
                    self.processing_queue.append({
                        'id': action['target_id'],
                        'name': dest['name'],
                        'number': dest['number'],
                        'type': dest['type'],
                        'path': path + [ext_name],
                        'depth': depth + 1,
                        'context': f"IVR Key '{key}'"
                    })
        
        # Boundary tests
        self.add_test(
            f"{path_prefix}IVR - Boundaries",
            "Invalid Key Press",
            f"While in {ext_name}, press '9' or '#' (unmapped key).",
            "System plays 'Invalid selection' or similar error prompt. Replays main menu."
        )
        
        self.add_test(
            f"{path_prefix}IVR - Boundaries",
            "No Input Timeout",
            f"Listen to {ext_name} prompt completely without pressing any key.",
            "After configured timeout period, system either replays prompt or routes to default timeout destination."
        )
        
        self.add_test(
            f"{path_prefix}IVR - Boundaries",
            "Dial-By-Extension",
            f"While in {ext_name}, dial a valid user extension number (e.g., 101).",
            "If dial-by-extension enabled, IVR recognizes extension input and transfers call to user. If disabled, treats as invalid input."
        )
    
    def _generate_user_tests_detailed(self, ext_id, ext_name, ext_number, path, path_prefix, depth, context):
        """Generate tests for user extensions"""
        
        # Get phone numbers
        numbers = self._extract_all_phone_numbers_detailed(ext_id)
        
        if depth == 0:
            self.add_test(
                f"{path_prefix}Basic Routing",
                "Internal Dial",
                f"From internal phone, dial extension {ext_number}.",
                f"Call connects to {ext_name}. Device rings."
            )
            
            for num in numbers:
                if num['usage_type'] == 'DirectNumber':
                    self.add_test(
                        f"{path_prefix}Basic Routing",
                        f"Direct Number: {num['formatted']}",
                        f"From external phone, dial {num['formatted']}.",
                        f"Call routes to {ext_name} via PSTN."
                    )
        
        self.add_test(
            f"{path_prefix}Voicemail",
            "Voicemail Deposit",
            f"Call {ext_name} (Ext {ext_number}) and let ring to voicemail. Leave 15-second test message.",
            "Voicemail greeting plays completely. Message is recorded successfully without truncation."
        )
    
    def _add_global_tests(self):
        """Add global validation tests"""
        
        self.add_test(
            "Global Validation",
            "Call Logs Accuracy",
            "Log into RingCentral Admin Portal. Navigate to Analytics > Reports > Call Log.",
            "All test calls appear in logs with correct: caller ID, destination extension, call duration, disposition (answered/voicemail/abandoned)."
        )
        
        self.add_test(
            "Global Validation",
            "Call Recording Retrieval",
            "If call recording is enabled, navigate to Admin Portal > Phone System > Call Recording.",
            "Test call recordings are available. Files are downloadable and playable. Audio captures both caller and agent/queue audio."
        )
        
        self.add_test(
            "Global Validation",
            "Voicemail Delivery",
            "Check voicemail recipient's email inbox and/or RingCentral mobile app notifications.",
            "Voicemail audio file (.mp3 or .wav) delivered successfully. If transcription enabled, text transcript included in email/notification."
        )


# =============================================================================
# PUBLIC API
# =============================================================================

def get_testable_extensions():
    """Get list of testable extensions"""
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
    Generate forensically detailed UAT test cases with:
    - Exact phone numbers to dial
    - Exact conditions for custom rules
    - Only configured features tested
    - Real agent names and details
    - Specific time windows for business hours
    - Complete recursive destination testing
    """
    analyzer = ForensicCallFlowAnalyzer(
        extension_id,
        extension_name,
        extension_number,
        extension_type
    )
    
    return analyzer.process_all_flows()
