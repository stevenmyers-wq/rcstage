# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches base call flows for the UI dropdown. Excludes Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site', 'ParkLocation']
    entities = [
        {"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']}
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    return sorted(entities, key=lambda x: x['name'])

def build_extension_map():
    """Builds a global directory including the TYPE of extension for recursive tracing."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    ext_map = {}
    if response and 'records' in response:
        for ext in response['records']:
            ext_map[str(ext['id'])] = {
                "name": ext.get('name', 'Unknown'),
                "ext": ext.get('extensionNumber', 'N/A'),
                "type": ext.get('type', 'Unknown')
            }
    return ext_map

def get_direct_numbers(ext_id):
    """Forensically extracts assigned DIDs."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = [r.get('phoneNumber') for r in response.get('records', []) if r.get('usageType') == 'DirectNumber']
    return ", ".join(numbers) if numbers else None

def get_business_hours_string(ext_id):
    """Extracts explicit schedules or falls back to Account-level."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
    if not response or 'schedule' not in response:
        response = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
        if not response or 'schedule' not in response:
            return "24/7 (Always Open)"
    
    schedule = response.get('schedule', {})
    if 'weeklyRanges' in schedule:
        days = []
        for day, times in schedule['weeklyRanges'].items():
            if times:
                days.append(f"{day[:3]} {times[0].get('from', '')}-{times[0].get('to', '')}")
        return ", ".join(days) if days else "Custom Schedule"
    return "Custom/Specific Schedule"

def resolve_target(action_obj, ext_map):
    """Resolves target IDs to Names and returns the ID for recursive tracing."""
    if not action_obj: return "Unknown", None
    
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map: 
        return ext_map[target_id]['name'], target_id
        
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map: 
        return f"Voicemail of {ext_map[target_id]['name']}", target_id
        
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}", None
    
    return "Default/Unknown Target", None

def get_answering_rules(ext_id):
    """Fetches all routing rules (Business Hours, After Hours, Custom) for the target."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
    return response.get('records', []) if response else []

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Recursive call flow tracer that generates exhaustive UAT scripts."""
    uat_cases = []
    case_counter = 1
    ext_map = build_extension_map()
    
    # The Processing Queue for recursive tracing
    targets_to_process = [{
        "id": str(extension_id),
        "name": extension_name,
        "ext": extension_number,
        "type": extension_type,
        "context": "Primary Flow"
    }]
    processed_ids = set()

    def add_case(category, scenario, step, expected):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{case_counter:04d}",
            "category": category,
            "scenario": scenario,
            "action": step,
            "expected": expected
        })
        case_counter += 1

    def queue_next_hop(target_id, reason_context):
        """Adds a discovered overflow target to the processing queue to map the whole journey."""
        if target_id and target_id in ext_map and target_id not in processed_ids:
            # We don't need to recursively map standard Users, just Call Flows
            if ext_map[target_id]['type'] in ['Department', 'IvrMenu', 'SharedLinesGroup']:
                targets_to_process.append({
                    "id": target_id,
                    "name": ext_map[target_id]['name'],
                    "ext": ext_map[target_id]['ext'],
                    "type": ext_map[target_id]['type'],
                    "context": reason_context
                })

    # START RECURSIVE CRAWLER LOOP
    while targets_to_process:
        current = targets_to_process.pop(0)
        
        # Prevent infinite loops (e.g., Queue A -> IVR B -> Queue A)
        if current['id'] in processed_ids:
            continue
        processed_ids.add(current['id'])

        c_id = current['id']
        c_name = current['name']
        c_ext = current['ext']
        c_type = current['type']
        
        # Prepend the context so the UAT document groups the hops logically
        ctx = f"[{current['context']}] " if current['context'] != "Primary Flow" else ""

        did_numbers = get_direct_numbers(c_id)
        bh_string = get_business_hours_string(c_id)
        answering_rules = get_answering_rules(c_id)

        # ==========================================
        # 1. INTEGRATION & TIME OF DAY (All Hops)
        # ==========================================
        if current['context'] == "Primary Flow":
            add_case(f"{ctx}Integration", "Internal Dialing", 
                     f"Dial extension {c_ext} ({c_name}) from an internal device.", 
                     "Call connects successfully without SIP errors.")
            
            if did_numbers:
                add_case(f"{ctx}Integration", "External Dialing (DID)", 
                         f"Dial the DID assigned to {c_name} ({did_numbers}) from a mobile phone.", 
                         "Call connects via the PSTN with high-quality, two-way audio.")
        
        add_case(f"{ctx}Routing Logic", "Business Hours (In-Hours)", 
                 f"Initiate a test call into {c_name} during Open Hours: [{bh_string}].", 
                 "Call follows the primary active routing rules.")

        # Parse explicitly configured After Hours or Custom Rules
        for rule in answering_rules:
            if not rule.get('enabled', False): continue
            
            rule_type = rule.get('type')
            if rule_type == 'AfterHours':
                ah_action = rule.get('callHandlingAction', 'Unknown')
                ah_name, ah_id = resolve_target(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), ext_map)
                
                add_case(f"{ctx}Routing Logic", "After Hours (Out-of-Hours)", 
                         f"Initiate a test call into {c_name} OUTSIDE of Open Hours: [{bh_string}].", 
                         f"Call intercepts and executes After Hours routing: [{ah_action}] -> {ah_name}.")
                
                # RECURSIVE HOP: Follow After Hours Target
                queue_next_hop(ah_id, f"Overflow (After Hours) from {c_name}")
            
            elif rule_type == 'Custom':
                c_rule_name = rule.get('name', 'Custom Rule')
                c_action = rule.get('callHandlingAction', 'Unknown')
                c_target_name, c_target_id = resolve_target(rule.get('transfer') or rule.get('voicemail'), ext_map)
                
                add_case(f"{ctx}Routing Logic", f"Custom Rule Trigger: {c_rule_name}", 
                         f"Initiate a call matching the parameters for Custom Rule '{c_rule_name}'.", 
                         f"Call executes Custom routing: [{c_action}] -> {c_target_name}.")
                
                queue_next_hop(c_target_id, f"Overflow (Custom Rule '{c_rule_name}') from {c_name}")

        # ==========================================
        # 2. CALL QUEUE (DEPARTMENT) DEEP CRAWL
        # ==========================================
        if c_type == 'Department':
            q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/call-queue-info', method='GET', raise_error=False)
            
            if q_info:
                transfer_mode = q_info.get('transferMode', 'Simultaneous')
                agent_timeout = q_info.get('agentTimeout', 15)
                wrap_up_time = q_info.get('wrapUpTime', 0)
                hold_time = q_info.get('holdTime', 0)
                max_callers = q_info.get('maxCallers', 0)
                interrupt_period = q_info.get('holdAudioInterruptionPeriod', 0)
                
                hold_action = q_info.get('holdTimeExpirationAction', 'Unknown')
                max_call_action = q_info.get('maxCallersAction', 'Unknown')
                
                hold_name, hold_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
                max_name, max_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)

                # Call Queue Caller Experience
                if interrupt_period > 0:
                    add_case(f"{ctx}Queue Experience", f"Interrupt Audio ({interrupt_period}s)", 
                             f"Remain on hold in {c_name} for at least {interrupt_period + 5} seconds.", 
                             f"At exactly {interrupt_period} seconds, hold music pauses, the interrupt prompt plays, and music resumes.")

                # Agent Operations & Wrap Up
                add_case(f"{ctx}Queue Agents", "Agent Opt-In/Opt-Out", 
                         f"Agent toggles 'Accept Queue Calls' to OFF in the RC App. Place a call into {c_name}.", 
                         "Agent's device does NOT ring. Call smoothly hunts to the next available agent.")

                if wrap_up_time > 0:
                    add_case(f"{ctx}Queue Agents", f"Wrap-Up (ACW) Timer ({wrap_up_time}s)", 
                             f"Agent answers a queue call and hangs up. Immediately place a second call into {c_name}.", 
                             f"Agent enters Wrap-Up status and does NOT ring again until the {wrap_up_time} second timer expires.")

                # Distribution Logic
                if transfer_mode == 'Simultaneous':
                    add_case(f"{ctx}Queue Distribution", f"Mode: {transfer_mode}", 
                             f"Ensure multiple agents are 'Available'. Place a call into {c_name}.", 
                             "The call rings ALL available queue members at the exact same time.")
                else:
                    add_case(f"{ctx}Queue Distribution", f"Mode: {transfer_mode}", 
                             f"Ensure multiple agents are 'Available'. Place a call into {c_name}.", 
                             f"The call cascades to agents sequentially based on {transfer_mode} logic.")
                    
                    add_case(f"{ctx}Queue Distribution", f"Agent Ring Timeout ({agent_timeout}s)", 
                             f"Targeted agent lets the call ring without answering for exactly {agent_timeout} seconds.", 
                             f"The {agent_timeout}s timer expires. The call drops from Agent 1 and begins ringing Agent 2.")

                # Hard Boundaries & Overflows
                add_case(f"{ctx}Queue Overflow", "Zero Agents Logged In", 
                         f"Ensure ALL agents assigned to {c_name} are Logged Out or on DND. Initiate a call.", 
                         "Call instantly bypasses queue hold music and triggers 'No Members Available' routing.")

                if hold_time > 0:
                    add_case(f"{ctx}Queue Overflow", f"Max Wait Time Reached ({hold_time}s)", 
                             f"Remain on hold in {c_name} until the {hold_time} second limit is reached.", 
                             f"Timer expires. Call executes overflow: [{hold_action}] -> {hold_name}.")
                    queue_next_hop(hold_id, f"Overflow (Max Wait Time) from {c_name}")
                
                if max_callers > 0:
                    add_case(f"{ctx}Queue Overflow", f"Max Callers Breached ({max_callers})", 
                             f"Simultaneously flood {c_name} with {max_callers + 1} concurrent inbound calls.", 
                             f"The final call breaches the capacity limit of {max_callers}. It instantly executes overflow: [{max_call_action}] -> {max_name}.")
                    queue_next_hop(max_id, f"Overflow (Max Callers) from {c_name}")

        # ==========================================
        # 3. IVR MENU DEEP CRAWL
        # ==========================================
        elif c_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{c_id}', method='GET', raise_error=False)
            if ivr_info:
                add_case(f"{ctx}IVR Prompts", "Barge-in (Interruptibility)", 
                         f"Dial {c_name}. While the greeting is actively playing, press a valid menu key.", 
                         "IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the full greeting.")
                
                if 'actions' in ivr_info:
                    for act in ivr_info['actions']:
                        key = act.get('input', '')
                        if not key: continue 
                        
                        act_type = act.get('action', 'Unknown')
                        target_name, target_id = resolve_target(act, ext_map)
                        
                        add_case(f"{ctx}IVR Navigation", f"Key Mapping: Press '{key}'", 
                                 f"Listen to prompt in {c_name} and press '{key}' on the dialpad.", 
                                 f"Executes: [{act_type}] -> {target_name}.")
                        
                        # RECURSIVE HOP: Follow the IVR Key Target
                        queue_next_hop(target_id, f"Path (Key {key}) from {c_name}")
                
                add_case(f"{ctx}IVR Boundaries", "Invalid Key Press", 
                         f"Press an unassigned key in {c_name} (e.g., '9' or '#').", 
                         "System plays an 'Invalid entry' error prompt and replays the menu.")
                add_case(f"{ctx}IVR Boundaries", "Timeout (No Input)", 
                         f"Listen to the entire prompt in {c_name} and provide no DTMF input.", 
                         "System times out, replays the menu, and eventually executes the default timeout routing.")

    # Loop Completes. Add final Administrative test.
    add_case("Administration", "Call Log Generation", 
             "Review the test calls in the RingCentral Analytics or Call Log portal.", 
             "System generated accurate Call Log data reflecting the entire traced journey.")

    return uat_cases
