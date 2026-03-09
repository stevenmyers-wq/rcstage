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
        self._build_extension_map()
        self._recursive_crawl(start_id, path=[], depth=0)
        return {
            'graph': self.flow_graph,
            'paths': self.paths,
            'ext_map': self.ext_map
        }
        
    def _build_extension_map(self):
        response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
        if response and 'records' in response:
            for ext in response['records']:
                self.ext_map[str(ext['id'])] = {
                    'name': ext.get('name', 'Unknown'),
                    'extension_number': ext.get('extensionNumber', 'N/A'),
                    'type': ext.get('type', 'Unknown')
                }
                
    def _recursive_crawl(self, ext_id, path, depth):
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
        
        # 1. Extract Answering Rules (This holds Queue Limits, Custom Rules, After Hours)
        rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
        if rules and 'records' in rules:
            node['answering_rules'] = rules['records']
            
            for rule in rules['records']:
                if not rule.get('enabled', False): continue
                
                # Check for queue object inside rule (Departments) to find overflows
                if rule.get('type') == 'BusinessHours' and ext_type == 'Department':
                    q = rule.get('queue', {})
                    targ1 = self._extract_target_id(q.get('transfer') or q.get('voicemail') or q.get('unconditionalForwarding') or rule.get('transfer') or rule.get('voicemail'))
                    if targ1 and targ1 in self.ext_map and self.ext_map[targ1]['type'] in ['Department', 'IvrMenu']:
                        self._recursive_crawl(targ1, path + [ext_id], depth + 1)
                        
                # Check standard rule overrides (AfterHours, Custom)
                if rule.get('type') in ['AfterHours', 'Custom']:
                    targ2 = self._extract_target_id(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'))
                    if targ2 and targ2 in self.ext_map and self.ext_map[targ2]['type'] in ['Department', 'IvrMenu']:
                        self._recursive_crawl(targ2, path + [ext_id], depth + 1)

        # 2. Extract IVR Keys
        if ext_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{ext_id}', method='GET', raise_error=False) or {}
            node['ivr_info'] = ivr_info
            for action in ivr_info.get('actions', []):
                targ3 = self._extract_target_id(action)
                if targ3 and targ3 in self.ext_map and self.ext_map[targ3]['type'] in ['Department', 'IvrMenu']:
                    self._recursive_crawl(targ3, path + [ext_id], depth + 1)
                    
        self.flow_graph[ext_id] = node

    def _extract_target_id(self, action_obj):
        if not action_obj: return None
        if isinstance(action_obj, list):
            if len(action_obj) > 0: action_obj = action_obj[0]
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
    """STRICT validation. Ensures DID belongs strictly to this exact extension ID."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = []
    if response and 'records' in response:
        for record in response['records']:
            ext_obj = record.get('extension', {})
            if record.get('usageType') == 'DirectNumber' and str(ext_obj.get('id', '')) == str(ext_id):
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
    if not action_obj: return "Disconnect / Operator"
    if isinstance(action_obj, list) and len(action_obj) > 0:
        action_obj = action_obj[0]
        
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"{ext_map[target_id]['name']} (Ext {ext_map[target_id]['extension_number']})"
    
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"Voicemail of {ext_map[target_id]['name']}"
    
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}"
    
    return "Operator / Default Target"

def parse_custom_conditions(rule):
    """Accurately parses custom rule configurations."""
    conditions = []
    if rule.get('callers'):
        c_ids = [c.get('callerId') or c.get('name') or 'Unknown' for c in rule['callers']]
        conditions.append(f"Caller ID is {', '.join(c_ids)}")
    if rule.get('calledNumbers'):
        nums = [n.get('phoneNumber', 'Unknown') for n in rule['calledNumbers']]
        conditions.append(f"Dialed Number is {', '.join(nums)}")
    if rule.get('schedule'):
        conditions.append("Specific Time/Date schedule matches")
    return " AND ".join(conditions) if conditions else "No specific conditions"

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

    for node_id, node_data in flow_graph.items():
        n_name = node_data['name']
        n_type = node_data['type']
        n_ext = ext_map[node_id]['extension_number']
        cat_prefix = f"[{n_name}] "
        
        did_numbers = get_direct_numbers(node_id)
        bh_string = get_business_hours_string(node_id)

        # =========================================================================
        # PHASE 1: CONNECTIVITY & ROUTING
        # =========================================================================
        if node_id == str(extension_id):
            if did_numbers:
                add_case("Integration", "External Routing (DID)", f"Dial the assigned DID for {n_name}: {did_numbers}.", f"Call successfully connects to {n_name} via PSTN with two-way audio.", prefix=cat_prefix)
            else:
                add_case("Integration", "Internal Routing", f"Dial {n_ext} from an internal device or via Auto-Receptionist.", f"Call successfully connects to {n_name} without dead air.", prefix=cat_prefix)
                
        add_case("Routing", "Business Hours (In-Hours)", f"Initiate call during Open Hours: [{bh_string}].", f"Call follows standard Active routing for {n_name}.", prefix=cat_prefix)

        # Extract Rules (Custom, After Hours, and BusinessHours for Queue data)
        queue_data = {}
        has_intro_greeting = False
        
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
            elif r_type == 'BusinessHours' and n_type == 'Department':
                queue_data = rule.get('queue', {})
                # Validate if an Introductory Greeting is actually configured and turned ON
                for greeting in rule.get('greetings', []):
                    if greeting.get('type') == 'Introductory':
                        has_intro_greeting = True

        # =========================================================================
        # PHASE 2: QUEUE EXHAUSTIVE TESTING
        # =========================================================================
        if n_type == 'Department':
            t_mode = queue_data.get('transferMode', 'Simultaneous')
            agent_timeout = queue_data.get('agentTimeout', 15)
            wrap_up_time = queue_data.get('wrapUpTime', 0)
            hold_time = queue_data.get('holdTime', 0)
            max_callers = queue_data.get('maxCallers', 0)
            interrupt_period = queue_data.get('holdAudioInterruptionPeriod', 0)
            
            b_rule = next((r for r in node_data.get('answering_rules', []) if r.get('type') == 'BusinessHours'), {})
            dest_obj = queue_data.get('transfer') or queue_data.get('voicemail') or b_rule.get('transfer') or b_rule.get('voicemail')
            overflow_dest = resolve_target_name(dest_obj, ext_map)

            # ONLY generate the greeting test if the API confirms it is enabled
            if has_intro_greeting:
                add_case("Queue Experience", "Introductory Greeting", f"Place a call to {n_name}.", "The Intro Greeting plays fully before agent ringing begins.", prefix=cat_prefix)
                
            add_case("Queue Experience", "Connecting Audio (Hold Music)", f"Remain in {n_name} while agents ring.", "Hold Music plays cleanly without jitter.", prefix=cat_prefix)
            if interrupt_period > 0:
                add_case("Queue Experience", f"Interrupt Audio ({interrupt_period}s)", f"Remain on hold in {n_name} for at least {interrupt_period + 5} seconds.", f"At exactly {interrupt_period}s, music pauses, interrupt prompt plays, then music resumes.", prefix=cat_prefix)

            add_case("Queue Agents", "Queue Opt-In", f"Agent toggles 'Accept Queue Calls' ON for {n_name}. Place test call.", f"Agent's device rings. Queue Name '{n_name}' prepends Caller ID.", prefix=cat_prefix)
            add_case("Queue Agents", "Queue Opt-Out / DND", f"Agent toggles 'Accept Queue Calls' OFF. Place test call.", "Agent's device does NOT ring. Call smoothly hunts to next available agent.", prefix=cat_prefix)
            
            if wrap_up_time > 0:
                add_case("Queue Agents", f"Wrap-Up (ACW) Timer ({wrap_up_time}s)", f"Agent answers a queue call and hangs up. Immediately place a second call into {n_name}.", f"Agent enters 'Wrap-Up' status and does NOT ring again until the {wrap_up_time}s timer expires.", prefix=cat_prefix)

            add_case("Queue Distribution", f"Distribution Method: {t_mode}", f"Ensure multiple agents are 'Available'. Place a call into {n_name}.", f"Call distributes to agents based purely on {t_mode} logic.", prefix=cat_prefix)
            if t_mode != 'Simultaneous':
                add_case("Queue Distribution", f"Agent Ring Timeout ({agent_timeout}s)", f"Targeted agent does not answer for exactly {agent_timeout} seconds.", f"Timer expires. Call immediately drops from Agent 1 and rings next available.", prefix=cat_prefix)

            add_case("Queue Overflows", "Zero Agents Logged In", f"Log ALL agents out of {n_name}. Initiate call.", f"Call bypasses queue ringing and immediately executes Overflow -> {overflow_dest}.", prefix=cat_prefix)

            if hold_time > 0:
                add_case("Queue Overflows", f"Max Wait Time Limit ({hold_time}s)", f"Remain on hold in {n_name} until {hold_time}s limit reached.", f"Timer expires. Call removed from queue and executes overflow -> {overflow_dest}.", prefix=cat_prefix)
            else:
                add_case("Queue Overflows", "Unlimited Wait Time", f"Remain on hold in {n_name} for 5+ minutes.", "Call does NOT drop or overflow. Remains in queue indefinitely as configured.", prefix=cat_prefix)
                
            if max_callers > 0:
                add_case("Queue Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously flood {n_name} with {max_callers + 1} concurrent calls.", f"Final call breaches capacity limit. Instantly executes overflow -> {overflow_dest}.", prefix=cat_prefix)

            add_case("Queue Overflows", "Zero-Out Option", f"While listening to {n_name} hold music, press '0'.", "Call escapes the queue and routes to the designated Operator or Voicemail recipient.", prefix=cat_prefix)

        # =========================================================================
        # PHASE 3: IVR EXHAUSTIVE TESTING
        # =========================================================================
        elif n_type == 'IvrMenu':
            ivr_info = node_data.get('ivr_info', {})
            add_case("IVR Tests", "Audio Quality & Script", f"Dial {n_name}.", "Prompt plays cleanly. Wording matches the approved script.", prefix=cat_prefix)
            
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    target = resolve_target_name(act, ext_map)
                    add_case("IVR Routing", f"Key Mapping: Press '{key}'", f"Listen to prompt and press '{key}'.", f"System processes input and routes call strictly to -> {target}.", prefix=cat_prefix)

            add_case("IVR Boundaries", "Dial-By-Extension", f"While in {n_name}, enter a known user's 3 or 4-digit extension.", "If enabled, IVR intercepts the string and transfers call to the user.", prefix=cat_prefix)
            add_case("IVR Boundaries", "Invalid Key Press", f"Press an unassigned key in {n_name} (e.g., '9' or '#').", "System plays 'Invalid entry' error prompt and replays main menu.", prefix=cat_prefix)

    # 4. Global Validation
    add_case("Global Validation", "Call Logs Generation", "Log into the Admin Portal and navigate to Analytics > Call Logs.", "All test calls are accurately reflected, showing the correct Caller ID, target extensions, duration, and final result.", prefix="[Account] ")

    return uat_cases
