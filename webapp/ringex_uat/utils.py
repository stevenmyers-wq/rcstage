# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

# =============================================================================
# CALL FLOW CRAWLER - Recursively maps the entire call routing structure
# =============================================================================

class CallFlowCrawler:
    """Recursively crawls RingCentral call flows to build a complete routing graph."""
    
    def __init__(self):
        self.ext_map = {}
        self.visited_nodes = set()
        self.flow_graph = {}
        self.paths = []
        
    def crawl(self, start_id):
        """Entry point: crawl from a starting extension and map all paths."""
        self._build_extension_map()
        self._recursive_crawl(start_id, path=[], depth=0)
        return {
            'graph': self.flow_graph,
            'paths': self.paths,
            'ext_map': self.ext_map
        }
        
    def _build_extension_map(self):
        """Build complete extension directory including types."""
        response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
        if response and 'records' in response:
            for ext in response['records']:
                self.ext_map[str(ext['id'])] = {
                    'name': ext.get('name', 'Unknown'),
                    'extension_number': ext.get('extensionNumber', 'N/A'),
                    'type': ext.get('type', 'Unknown')
                }
                
    def _recursive_crawl(self, ext_id, path, depth):
        """Recursively explore the call flow tree."""
        # Prevent infinite routing loops and excessive depth
        if depth > 15 or ext_id in self.visited_nodes:
            return
            
        self.visited_nodes.add(ext_id)
        ext_id = str(ext_id)
        
        if ext_id not in self.ext_map:
            return
            
        ext_info = self.ext_map[ext_id]
        ext_type = ext_info['type']
        
        node = {
            'id': ext_id,
            'name': ext_info['name'],
            'type': ext_type,
            'children': [],
            'answering_rules': []
        }
        
        # 1. ALWAYS crawl answering rules first (for After Hours / Custom Rules)
        rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
        if rules and 'records' in rules:
            node['answering_rules'] = rules['records']
            for rule in rules['records']:
                if not rule.get('enabled', False): continue
                
                target_id = self._extract_target_id(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'))
                if target_id and target_id in self.ext_map and self.ext_map[target_id]['type'] in ['Department', 'IvrMenu']:
                    new_path = path + [ext_id]
                    self._recursive_crawl(target_id, new_path, depth + 1)
        
        # 2. Process based on extension type
        if ext_type == 'Department':
            node['children'] = self._crawl_call_queue(ext_id, path, depth)
        elif ext_type == 'IvrMenu':
            node['children'] = self._crawl_ivr_menu(ext_id, path, depth)
        else:
            node['terminal'] = True
            
        self.flow_graph[ext_id] = node
        
        if node.get('terminal') or depth > 10:
            self.paths.append(path + [ext_id])
            
    def _crawl_call_queue(self, queue_id, path, depth):
        """Extract all routing destinations from a call queue."""
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{queue_id}/call-queue-info', method='GET', raise_error=False) or {}
        children = []
        new_path = path + [queue_id]
        
        overflow_actions = [
            ('maxCallersAction', 'max_callers_overflow'),
            ('holdTimeExpirationAction', 'max_wait_overflow'),
            ('noAnswerAction', 'no_agents_overflow')
        ]
        
        # Map specific Queue Overflows to the tree
        for action_key, label in overflow_actions:
            action = q_info.get(action_key)
            if action:
                target_id = self._extract_target_id(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'))
                if target_id and target_id in self.ext_map and self.ext_map[target_id]['type'] in ['Department', 'IvrMenu']:
                    children.append({'target_id': target_id, 'trigger': label})
                    self._recursive_crawl(target_id, new_path, depth + 1)
        return children
        
    def _crawl_ivr_menu(self, ivr_id, path, depth):
        """Extract all menu options from an IVR."""
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{ivr_id}', method='GET', raise_error=False) or {}
        children = []
        new_path = path + [ivr_id]
        
        for action in ivr_info.get('actions', []):
            target_id = self._extract_target_id(action)
            if target_id and target_id in self.ext_map and self.ext_map[target_id]['type'] in ['Department', 'IvrMenu']:
                children.append({'target_id': target_id, 'trigger': f"ivr_key_{action.get('input', '')}"})
                self._recursive_crawl(target_id, new_path, depth + 1)
        return children
        
    def _extract_target_id(self, action_obj):
        if not action_obj: return None
        if action_obj.get('extension', {}).get('id'): return str(action_obj['extension']['id'])
        if action_obj.get('recipient', {}).get('id'): return str(action_obj['recipient']['id'])
        return None

# =============================================================================
# UAT TEST CASE GENERATOR - Path-based exhaustive test generation
# =============================================================================

def get_testable_extensions():
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response: return []
    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [{"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']} for ext in response['records'] if ext.get('type') in valid_types]
    return sorted(entities, key=lambda x: x['name'])

def get_direct_numbers(ext_id):
    """STRICT validation to prevent pulling unrelated site numbers."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = []
    if response and 'records' in response:
        for record in response['records']:
            if record.get('usageType') == 'DirectNumber' and str(record.get('extension', {}).get('id', '')) == str(ext_id):
                numbers.append(record.get('phoneNumber'))
    return ", ".join(numbers) if numbers else None

def get_business_hours_string(ext_id):
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
    if not response or 'schedule' not in response:
        response = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
        if not response or 'schedule' not in response: return "24/7 (Always Open)"
    schedule = response.get('schedule', {})
    if 'weeklyRanges' in schedule:
        days = [f"{day[:3]} {times[0].get('from', '')}-{times[0].get('to', '')}" for day, times in schedule['weeklyRanges'].items() if times]
        return ", ".join(days) if days else "Custom Schedule"
    return "24/7 (Always Open)"

def resolve_target_name(action_obj, ext_map):
    """Explicitly maps API JSON to actual human-readable targets."""
    if not action_obj: return "Disconnect / Default Configuration"
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"{ext_map[target_id]['name']} (Ext {ext_map[target_id]['extension_number']})"
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"Voicemail of {ext_map[target_id]['name']} (Ext {ext_map[target_id]['extension_number']})"
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}"
    return "Unknown Configured Target"

def parse_custom_conditions(rule):
    conditions = []
    if 'callers' in rule and rule['callers']:
        c_ids = [c.get('callerId', '') for c in rule['callers']]
        conditions.append(f"Caller ID is {', '.join(c_ids)}")
    if 'calledNumbers' in rule and rule['calledNumbers']:
        nums = [n.get('phoneNumber', '') for n in rule['calledNumbers']]
        conditions.append(f"Dialed Number is {', '.join(nums)}")
    if 'schedule' in rule:
        conditions.append("Specific Time/Date")
    return " AND ".join(conditions) if conditions else "Unknown Condition"

def get_zero_out_target(ext_id, ext_map):
    rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
    if rules and 'records' in rules:
        for r in rules['records']:
            if r.get('type') == 'BusinessHours' and r.get('callHandlingAction') == 'TakeMessagesReturnToGreeting':
                vm_recipient_id = str(r.get('voicemail', {}).get('recipient', {}).get('id', ''))
                if vm_recipient_id in ext_map:
                    return f"{ext_map[vm_recipient_id]['name']} (Ext {ext_map[vm_recipient_id]['extension_number']})"
    return "None Configured"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    uat_cases = []
    case_counter = 1
    
    crawler = CallFlowCrawler()
    flow_data = crawler.crawl(extension_id)
    ext_map = flow_data['ext_map']
    flow_graph = flow_data['graph']

    def add_case(category, scenario, step, expected, prefix=""):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{case_counter:04d}",
            "category": f"{prefix}{category}",
            "scenario": scenario,
            "action": step,
            "expected": expected
        })
        case_counter += 1

    # Iterate through EVERY node discovered in the graph
    for node_id, node_data in flow_graph.items():
        n_name = node_data['name']
        n_type = node_data['type']
        n_ext = ext_map[node_id]['extension_number']
        cat_prefix = f"[{n_name}] "
        
        did_numbers = get_direct_numbers(node_id)
        bh_string = get_business_hours_string(node_id)

        # =========================================================================
        # PHASE 1: CONNECTIVITY & ROUTING (Applied to every node)
        # =========================================================================
        if node_id == str(extension_id):
            if did_numbers:
                add_case("Integration", "External Routing (DID)", f"Dial the assigned DID for {n_name}: {did_numbers}.", f"Call successfully connects to {n_name} via PSTN with two-way audio.", prefix=cat_prefix)
            else:
                add_case("Integration", "Internal Routing", f"Dial {n_ext} from an internal device or via Auto-Receptionist.", f"Call successfully connects to {n_name} without dead air.", prefix=cat_prefix)
                
        add_case("Routing", "Business Hours (In-Hours)", f"Initiate call during Open Hours: [{bh_string}].", f"Call follows standard Active routing for {n_name}.", prefix=cat_prefix)

        for rule in node_data.get('answering_rules', []):
            if not rule.get('enabled', False): continue
            r_type = rule.get('type')
            if r_type == 'AfterHours':
                ah_target = resolve_target_name(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), ext_map)
                add_case("Routing", "After Hours (Out-of-Hours)", f"Initiate call OUTSIDE of Open Hours: [{bh_string}].", f"Call executes After Hours routing -> Routes explicitly to: {ah_target}.", prefix=cat_prefix)
            elif r_type == 'Custom':
                c_name_rule = rule.get('name', 'Custom Rule')
                c_cond = parse_custom_conditions(rule)
                c_target = resolve_target_name(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), ext_map)
                add_case("Routing", f"Custom Rule: {c_name_rule}", f"Initiate a call where: {c_cond}.", f"Rule intercepts call -> Routes explicitly to: {c_target}.", prefix=cat_prefix)

        # =========================================================================
        # PHASE 2: QUEUE EXHAUSTIVE TESTING
        # =========================================================================
        if n_type == 'Department':
            q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{node_id}/call-queue-info', method='GET', raise_error=False) or {}
            
            t_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15)
            wrap_up_time = q_info.get('wrapUpTime', 0)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            interrupt_period = q_info.get('holdAudioInterruptionPeriod', 0)
            
            hold_dest = resolve_target_name(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
            max_dest = resolve_target_name(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
            zero_out_target = get_zero_out_target(node_id, ext_map)

            # Caller Experience
            add_case("Queue - Caller Experience", "Introductory Greeting", f"Place a call to {n_name}.", "If configured, the Intro Greeting plays fully before agent ringing begins.", prefix=cat_prefix)
            add_case("Queue - Caller Experience", "Connecting Audio (Hold Music)", f"Remain in {n_name} while agents ring.", "Hold Music plays cleanly without jitter.", prefix=cat_prefix)
            if interrupt_period > 0:
                add_case("Queue - Caller Experience", f"Interrupt Audio ({interrupt_period}s)", f"Remain on hold in {n_name} for at least {interrupt_period + 5} seconds.", f"At exactly {interrupt_period}s, music pauses, interrupt prompt plays, then music resumes.", prefix=cat_prefix)

            # Agent States
            add_case("Queue - Agent Tests", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON for {n_name}. Place test call.", f"Agent's device rings. Queue Name '{n_name}' prepends Caller ID.", prefix=cat_prefix)
            add_case("Queue - Agent Tests", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place test call.", "Agent's device does NOT ring. Call smoothly hunts to next available agent.", prefix=cat_prefix)
            add_case("Queue - Agent Tests", "Active Call Decline", f"While queue call is ringing agent, agent clicks 'Decline'.", "Ringing stops for that agent. Call hunts to next available agent without dropping caller.", prefix=cat_prefix)
            
            if wrap_up_time > 0:
                add_case("Queue - Agent Tests", f"Wrap-Up (ACW) Timer ({wrap_up_time}s)", f"Agent answers a queue call and hangs up. Immediately place a second call into {n_name}.", f"Agent enters 'Wrap-Up' status and does NOT ring again until the {wrap_up_time}s timer expires.", prefix=cat_prefix)
            else:
                add_case("Queue - Agent Tests", "No Wrap-Up Configured", f"Agent answers a queue call and hangs up. Immediately place a second call into {n_name}.", "Agent receives the new queue call immediately.", prefix=cat_prefix)

            # Distribution Logic
            add_case("Queue - Distribution", f"Distribution Method: {t_mode}", f"Ensure multiple agents are 'Available'. Place a call into {n_name}.", f"Call distributes to agents based purely on {t_mode} logic.", prefix=cat_prefix)
            if t_mode != 'Simultaneous':
                add_case("Queue - Distribution", f"Agent Ring Timeout ({agent_timeout}s)", f"Targeted agent does not answer for exactly {agent_timeout} seconds.", f"Timer expires. Call immediately drops from Agent 1 and rings next available.", prefix=cat_prefix)

            # Call Handling
            add_case("Queue - Call Handling", "Call Hold", "Agent answers queue call and places caller on hold via RC App.", "Caller hears agent hold music. Call is successfully retrieved.", prefix=cat_prefix)
            add_case("Queue - Call Handling", "Warm Transfer", "Agent answers call, initiates Warm Transfer to internal extension, consults, and completes.", "Caller is connected to secondary extension with two-way audio.", prefix=cat_prefix)
            add_case("Queue - Call Handling", "Blind Transfer", "Agent answers call and initiates Blind Transfer to internal extension.", "Agent is released. Caller hears ringing to secondary extension.", prefix=cat_prefix)
            add_case("Queue - Call Handling", "Call Park", "Agent answers call and Parks it to a Park Location (e.g., *801).", "Caller is parked. Call can be retrieved by dialing the park code.", prefix=cat_prefix)

            # Overflows
            add_case("Queue - Overflows", "Zero Agents Logged In", f"Log ALL agents out of {n_name}. Initiate call.", f"Call bypasses queue ringing and immediately executes Overflow -> {hold_dest}.", prefix=cat_prefix)

            if hold_time > 0:
                add_case("Queue - Overflows", f"Max Wait Time Limit ({hold_time}s)", f"Remain on hold in {n_name} until {hold_time}s limit reached.", f"Timer expires. Call removed from queue and executes overflow -> {hold_dest}.", prefix=cat_prefix)
            else:
                add_case("Queue - Overflows", "Unlimited Wait Time", f"Remain on hold in {n_name} for 10+ minutes.", "Call does NOT drop or overflow. Remains in queue indefinitely.", prefix=cat_prefix)
                
            if max_callers > 0:
                add_case("Queue - Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {n_name} with {max_callers + 1} concurrent calls.", f"Final call breaches capacity limit. Instantly executes overflow -> {max_dest}.", prefix=cat_prefix)
            else:
                add_case("Queue - Overflows", "Unlimited Queue Capacity", f"Place multiple concurrent calls into {n_name}.", "No calls are rejected or overflowed due to capacity limits.", prefix=cat_prefix)

            if zero_out_target != "None Configured":
                add_case("Queue - Overflows", "Zero-Out Exception", f"While listening to {n_name} hold music, press '0'.", f"Call immediately escapes queue and routes to -> {zero_out_target}.", prefix=cat_prefix)
            else:
                add_case("Queue - Overflows", "Zero-Out Disabled", f"While listening to {n_name} hold music, press '0'.", "Input is gracefully ignored. Call remains in queue.", prefix=cat_prefix)

        # =========================================================================
        # PHASE 3: IVR EXHAUSTIVE TESTING
        # =========================================================================
        elif n_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{node_id}', method='GET', raise_error=False) or {}
            
            add_case("IVR Menu Tests", "Audio Quality & Script", f"Dial {n_name}.", "Prompt plays cleanly. Wording matches the approved script.", prefix=cat_prefix)
            add_case("IVR Menu Tests", "Barge-in (Interruptibility)", f"While {n_name} greeting is playing, press a valid menu key.", "IVR registers DTMF tone immediately and routes call without forcing caller to listen to full greeting.", prefix=cat_prefix)
            
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    target = resolve_target_name(act, ext_map)
                    add_case("IVR Routing", f"Key Mapping: Press '{key}'", f"Listen to prompt and press '{key}'.", f"System processes input and routes call strictly to -> {target}.", prefix=cat_prefix)

            add_case("IVR Boundaries", "Dial-By-Extension", f"While in {n_name}, enter a known user's 3 or 4-digit extension.", "If enabled, IVR intercepts the string and transfers call to the user.", prefix=cat_prefix)
            add_case("IVR Boundaries", "Invalid Key Press", f"Press an unassigned key in {n_name} (e.g., '9' or '#').", "System plays 'Invalid entry' error prompt and replays main menu.", prefix=cat_prefix)
            add_case("IVR Boundaries", "Timeout (No Input)", f"Listen to entire prompt in {n_name} and provide no input.", "System times out, replays menu, and eventually executes default timeout routing.", prefix=cat_prefix)

    # =========================================================================
    # PHASE 4: GLOBAL VOICEMAIL & ADMIN
    # =========================================================================
    add_case("Global Validation", "Voicemail Deposit", "Trigger any tested routing scenario that routes to Voicemail. Leave a 15-second test message.", "Correct Voicemail greeting plays. Message is successfully recorded without truncating.", prefix="[Account] ")
    add_case("Global Validation", "Voicemail Delivery", "Check the designated target's inbox (Email Notification or RingCentral App).", "The voicemail audio file (.mp3) is delivered accurately.", prefix="[Account] ")
    add_case("Global Validation", "Call Logs Generation", "Log into the RingCentral Admin Portal and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct originating Caller ID, target extensions, duration, and final result.", prefix="[Account] ")
    add_case("Global Validation", "Call Recording Retrieval", "If Automatic Call Recording is enabled, navigate to the Call Recordings section in the Admin Portal.", "The recording of the test call is present, playable, and clearly captures both legs of the audio.", prefix="[Account] ")

    return uat_cases
