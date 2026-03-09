# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [
        {"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']}
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    return sorted(entities, key=lambda x: x['name'])

def build_extension_map():
    """Builds a directory containing name, extension, and TYPE for recursive tracing."""
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
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = [r.get('phoneNumber') for r in response.get('records', []) if r.get('usageType') == 'DirectNumber']
    return ", ".join(numbers) if numbers else None

def get_business_hours_string(ext_id):
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

def resolve_and_queue(action_obj, ext_map, current_name, reason_context, queue_list):
    """Resolves a target's name AND adds it to the crawler queue if it's a testable flow."""
    if not action_obj: 
        return "Unknown Configuration"
    
    target_id = None
    target_name = "Unknown Target"
    prefix = ""

    if 'extension' in action_obj:
        target_id = str(action_obj['extension'].get('id', ''))
    elif 'recipient' in action_obj:
        target_id = str(action_obj['recipient'].get('id', ''))
        prefix = "Voicemail of "
    elif 'phoneNumber' in action_obj:
        return f"External Number ({action_obj['phoneNumber']})"

    if target_id and target_id in ext_map:
        target_name = ext_map[target_id]['name']
        target_type = ext_map[target_id]['type']
        
        # If the target is a Queue or IVR, add it to the crawler to trace the journey
        if target_type in ['Department', 'IvrMenu', 'SharedLinesGroup']:
            queue_list.append({
                "id": target_id,
                "name": target_name,
                "ext": ext_map[target_id]['ext'],
                "type": target_type,
                "context": f"Overflow/Path from {current_name} ({reason_context})"
            })
            
        return f"{prefix}{target_name} (Ext {ext_map[target_id]['ext']})"
    
    return "Configured Destination"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    uat_cases = []
    case_counter = 1
    ext_map = build_extension_map()
    
    # The Master Crawler Queue
    nodes_to_process = [{
        "id": str(extension_id),
        "name": extension_name,
        "ext": extension_number,
        "type": extension_type,
        "context": "Primary Entry Point"
    }]
    
    # Keep track of where we've been to prevent infinite routing loops
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

    # ==========================================
    # RECURSIVE CRAWLER LOOP START
    # ==========================================
    while nodes_to_process:
        current = nodes_to_process.pop(0)
        c_id = current['id']
        c_name = current['name']
        c_ext = current['ext']
        c_type = current['type']
        c_context = current['context']
        
        if c_id in processed_ids:
            continue
        processed_ids.add(c_id)

        # Formatting prefixes to show the tester where they are in the tree
        cat_prefix = f"[{c_name}] "
        step_prefix = f"[Path: {c_context}] " if c_context != "Primary Entry Point" else ""
        
        did_numbers = get_direct_numbers(c_id)
        bh_string = get_business_hours_string(c_id)

        # --- 1. CONNECTIVITY (Only for Primary or DIDs) ---
        if c_context == "Primary Entry Point":
            add_case(f"{cat_prefix}Integration", "Internal Routing", 
                     f"Dial extension {c_ext} from an internal device.", 
                     f"Call connects successfully to {c_name} without SIP errors.")
            if did_numbers:
                add_case(f"{cat_prefix}Integration", "External Routing (DID)", 
                         f"Dial the DID assigned to {c_name} ({did_numbers}) from an external mobile.", 
                         "Call connects via the PSTN with high-quality, two-way audio.")

        # --- 2. ANSWERING RULES & SCHEDULES ---
        add_case(f"{cat_prefix}Routing", "Business Hours Validation", 
                 f"{step_prefix}Initiate a test call into {c_name} during Open Hours: [{bh_string}].", 
                 "Call follows the primary active 'Open' routing path.")
                 
        if bh_string != "24/7 (Always Open)":
            add_case(f"{cat_prefix}Routing", "Out-of-Hours Validation", 
                     f"{step_prefix}Initiate a test call into {c_name} OUTSIDE of Open Hours: [{bh_string}].", 
                     "Call intercepts and executes After Hours routing.")

        answering_rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/answering-rule', method='GET', raise_error=False)
        if answering_rules and 'records' in answering_rules:
            for rule in answering_rules['records']:
                if not rule.get('enabled', False): continue
                if rule.get('type') == 'Custom':
                    rule_name = rule.get('name', 'Custom Rule')
                    target = resolve_and_queue(rule.get('transfer') or rule.get('voicemail'), ext_map, c_name, f"Custom Rule: {rule_name}", nodes_to_process)
                    add_case(f"{cat_prefix}Routing", f"Custom Rule: {rule_name}", 
                             f"{step_prefix}Trigger the parameters for custom rule '{rule_name}'.", 
                             f"Call executes Custom routing -> {target}.")

        # --- 3. CALL QUEUE (DEPARTMENT) ---
        if c_type == 'Department':
            q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/call-queue-info', method='GET', raise_error=False) or {}
            
            transfer_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            
            hold_dest = resolve_and_queue(q_info.get('transfer') or q_info.get('voicemail'), ext_map, c_name, "Max Wait Time", nodes_to_process)
            max_dest = resolve_and_queue(q_info.get('transfer') or q_info.get('voicemail'), ext_map, c_name, "Max Callers", nodes_to_process)

            # Agent Exhaustive
            add_case(f"{cat_prefix}Agent Tests", "Queue Opt-In", 
                     f"{step_prefix}Agent logs into RC App and toggles 'Accept Queue Calls' ON for {c_name}. Place test call.", 
                     f"Agent's device rings. Queue Name '{c_name}' prepends Caller ID.")
            add_case(f"{cat_prefix}Agent Tests", "Queue Opt-Out / DND", 
                     f"{step_prefix}Agent toggles 'Accept Queue Calls' OFF. Place test call.", 
                     "Agent's device does NOT ring. Call smoothly hunts to next available agent.")
            add_case(f"{cat_prefix}Agent Tests", "Active Call Decline", 
                     f"{step_prefix}While queue call is ringing agent, agent clicks 'Decline'.", 
                     "Ringing stops for that agent immediately. Call hunts to next available agent without dropping caller.")

            # Distribution Exhaustive
            if transfer_mode == 'Simultaneous':
                add_case(f"{cat_prefix}Distribution", "Simultaneous Ringing", 
                         f"{step_prefix}Ensure multiple agents available. Place call into {c_name}.", 
                         "Call rings ALL available queue members at the exact same time.")
            else:
                add_case(f"{cat_prefix}Distribution", f"Sequential ({transfer_mode})", 
                         f"{step_prefix}Ensure multiple agents available. Place call into {c_name}.", 
                         f"Call cascades to agents based on {transfer_mode} logic.")
                add_case(f"{cat_prefix}Distribution", f"Agent Ring Timeout ({agent_timeout}s)", 
                         f"{step_prefix}Targeted agent lets call ring without answering for {agent_timeout}s.", 
                         f"{agent_timeout}s timer expires. Call drops from Agent 1 and rings Agent 2.")

            # Boundaries Exhaustive
            add_case(f"{cat_prefix}Overflows", "Zero Agents Logged In", 
                     f"{step_prefix}Ensure ALL assigned agents are Logged Out or on DND. Initiate call.", 
                     "Call bypasses queue hold music and triggers 'No Members Available' routing.")
            
            if hold_time > 0:
                add_case(f"{cat_prefix}Overflows", f"Max Wait Time ({hold_time}s)", 
                         f"{step_prefix}Remain on hold in {c_name} until {hold_time}s limit reached.", 
                         f"{hold_time}s timer expires. Call removed from queue and executes -> {hold_dest}.")
            
            if max_callers > 0:
                add_case(f"{cat_prefix}Overflows", f"Max Callers Limit ({max_callers})", 
                         f"{step_prefix}Simultaneously flood {c_name} with {max_callers + 1} concurrent calls.", 
                         f"Final call breaches {max_callers} capacity limit. Instantly executes -> {max_dest}.")

            add_case(f"{cat_prefix}Overflows", "Zero-Out Exception", 
                     f"{step_prefix}While listening to {c_name} hold music, press '0'.", 
                     "If zero-out operator configured, call escapes queue. If not, DTMF gracefully ignored.")

        # --- 4. IVR MENU ---
        elif c_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{c_id}', method='GET', raise_error=False) or {}
            
            add_case(f"{cat_prefix}IVR Prompts", "Barge-in (Interruptibility)", 
                     f"{step_prefix}While {c_name} greeting is playing, press a valid menu key.", 
                     "IVR registers DTMF tone immediately and routes call without forcing caller to listen to full greeting.")
            
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    target = resolve_and_queue(act, ext_map, c_name, f"Key Press '{key}'", nodes_to_process)
                    
                    add_case(f"{cat_prefix}IVR Routing", f"Key Mapping: Press '{key}'", 
                             f"{step_prefix}Listen to prompt and press '{key}' on dialpad.", 
                             f"System processes input and routes call -> {target}.")
            
            add_case(f"{cat_prefix}IVR Boundaries", "Dial-By-Extension Verification", 
                     f"{step_prefix}While in {c_name}, enter a known internal user's 3/4-digit extension.", 
                     "If general extension dialing enabled, IVR intercepts string and transfers call to user.")
            add_case(f"{cat_prefix}IVR Boundaries", "Invalid Key Press", 
                     f"{step_prefix}Press an unassigned key (e.g., '9' or '#').", 
                     "System plays 'Invalid entry' prompt and replays main menu.")
            add_case(f"{cat_prefix}IVR Boundaries", "Timeout (No Input)", 
                     f"{step_prefix}Listen to entire IVR prompt and provide no DTMF input.", 
                     "System times out, replays menu, and eventually executes default timeout routing.")

    # Wrap up with global administration
    add_case("Global Administration", "Call Logs Generation", 
             "Log into the Admin Portal and navigate to Analytics > Call Logs.", 
             "All test calls are accurately reflected, showing correct originating Caller ID, target extensions, duration, and final result across the entire traced journey.")

    return uat_cases
