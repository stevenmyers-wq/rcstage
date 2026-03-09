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
    """STRICTLY extracts only the DIDs specifically assigned to this exact extension ID."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = []
    if response and 'records' in response:
        for record in response['records']:
            # Prevent pulling unrelated site/company numbers by validating the extension ID binding
            if record.get('usageType') == 'DirectNumber' and str(record.get('extension', {}).get('id', '')) == str(ext_id):
                numbers.append(record.get('phoneNumber'))
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
    return "24/7 (Always Open)"

def parse_custom_conditions(rule):
    """Reads the exact triggers for a custom rule so the tester knows HOW to trigger it."""
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

def resolve_target(action_obj, ext_map):
    """Forensically resolves actions to human-readable targets and returns the ID for recursive tracing."""
    if not action_obj: 
        return "Disconnect / Default Configuration", None
    
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return f"{ext_map[target_id]['name']} (Ext {ext_map[target_id]['ext']})", target_id
        
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return f"Voicemail of {ext_map[target_id]['name']} (Ext {ext_map[target_id]['ext']})", target_id
        
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}", None
    
    return "Default Configuration", None

def get_zero_out_target(ext_id, ext_map):
    """Attempts to find the Voicemail/Operator recipient which acts as the '0' option."""
    rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', method='GET', raise_error=False)
    if rules and 'records' in rules:
        for r in rules['records']:
            if r.get('type') == 'BusinessHours' and r.get('callHandlingAction') == 'TakeMessagesReturnToGreeting':
                vm_recipient_id = str(r.get('voicemail', {}).get('recipient', {}).get('id', ''))
                if vm_recipient_id in ext_map:
                    return f"{ext_map[vm_recipient_id]['name']} (Ext {ext_map[vm_recipient_id]['ext']})"
    return "None Configured"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    uat_cases = []
    case_counter = 1
    ext_map = build_extension_map()
    
    nodes_to_process = [{
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

    while nodes_to_process:
        current = nodes_to_process.pop(0)
        c_id = current['id']
        c_name = current['name']
        c_ext = current['ext']
        c_type = current['type']
        c_context = current['context']
        
        if c_id in processed_ids: continue
        processed_ids.add(c_id)

        cat_prefix = f"[{c_name}] "
        step_prefix = f"[Path: {c_context}] " if c_context != "Primary Flow" else ""
        
        did_numbers = get_direct_numbers(c_id)
        bh_string = get_business_hours_string(c_id)
        answering_rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/answering-rule', method='GET', raise_error=False) or {'records': []}

        # --- 1. CONNECTIVITY ---
        if c_context == "Primary Flow":
            if did_numbers:
                add_case(f"{cat_prefix}Integration", "External Routing (DID)", 
                         f"Dial the strictly assigned DID: {did_numbers}.", 
                         f"Call successfully connects to {c_name} via PSTN.")
            else:
                add_case(f"{cat_prefix}Integration", "Internal/Auto-Receptionist Routing", 
                         f"Dial {c_ext} from an internal device or via the Main Number.", 
                         f"Call successfully connects to {c_name}.")

        # --- 2. ANSWERING RULES (Deep Parse) ---
        for rule in answering_rules.get('records', []):
            if not rule.get('enabled', False): continue
            
            r_type = rule.get('type')
            if r_type == 'BusinessHours':
                add_case(f"{cat_prefix}Routing", "Business Hours (In-Hours)", 
                         f"{step_prefix}Initiate call during Open Hours: [{bh_string}].", 
                         f"Call follows standard Active routing for {c_name}.")
                         
            elif r_type == 'AfterHours':
                ah_action = rule.get('callHandlingAction', 'Unknown')
                ah_target, ah_id = resolve_target(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), ext_map)
                
                add_case(f"{cat_prefix}Routing", "After Hours (Out-of-Hours)", 
                         f"{step_prefix}Initiate call OUTSIDE of Open Hours: [{bh_string}].", 
                         f"Call executes After Hours Action [{ah_action}] -> Routes exactly to {ah_target}.")
                
                if ah_id and ah_id not in processed_ids and ext_map.get(ah_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": ah_id, "name": ext_map[ah_id]['name'], "ext": ext_map[ah_id]['ext'], "type": ext_map[ah_id]['type'], "context": f"After Hours from {c_name}"})
            
            elif r_type == 'Custom':
                c_name_rule = rule.get('name', 'Custom Rule')
                c_cond = parse_custom_conditions(rule)
                c_action = rule.get('callHandlingAction', 'Unknown')
                c_target, c_id = resolve_target(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), ext_map)
                
                add_case(f"{cat_prefix}Routing", f"Custom Rule: {c_name_rule}", 
                         f"{step_prefix}Initiate a call where: {c_cond}.", 
                         f"Rule '{c_name_rule}' intercepts call. Executes [{c_action}] -> Routes exactly to {c_target}.")

        # --- 3. QUEUE SPECIFIC TESTS ---
        if c_type == 'Department':
            q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/call-queue-info', method='GET', raise_error=False) or {}
            
            transfer_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            
            hold_dest_name, hold_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
            max_dest_name, max_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
            zero_out_target = get_zero_out_target(c_id, ext_map)

            # Routing Logic
            add_case(f"{cat_prefix}Distribution", f"Distribution Method: {transfer_mode}", 
                     f"{step_prefix}Ensure multiple agents available. Place call into {c_name}.", 
                     f"Call distributes to agents based purely on {transfer_mode} logic.")

            if transfer_mode != 'Simultaneous':
                add_case(f"{cat_prefix}Distribution", f"Agent Ring Timeout ({agent_timeout}s)", 
                         f"{step_prefix}Targeted agent does not answer for exactly {agent_timeout} seconds.", 
                         f"Timer expires. Call immediately drops from Agent 1 and rings next available.")

            # Hard Boundaries & Overflows
            add_case(f"{cat_prefix}Overflows", "Zero Agents Logged In", 
                     f"{step_prefix}Log ALL assigned agents completely out or set to DND. Initiate call.", 
                     f"Call bypasses queue ringing and immediately executes Max Wait Time Overflow -> {hold_dest_name}.")

            if hold_time > 0:
                add_case(f"{cat_prefix}Overflows", f"Max Wait Time Reached ({hold_time}s)", 
                         f"{step_prefix}Remain on hold in {c_name} until {hold_time} seconds elapse.", 
                         f"Call executes Overflow -> {hold_dest_name}.")
                if hold_id and hold_id not in processed_ids and ext_map.get(hold_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": hold_id, "name": ext_map[hold_id]['name'], "ext": ext_map[hold_id]['ext'], "type": ext_map[hold_id]['type'], "context": f"Wait Time Overflow from {c_name}"})
            else:
                add_case(f"{cat_prefix}Overflows", "Unlimited Wait Time Configured", 
                         f"{step_prefix}Remain on hold in {c_name} for 5+ minutes.", 
                         "Call does NOT drop or overflow. Remains in queue indefinitely as configured.")
            
            if max_callers > 0:
                add_case(f"{cat_prefix}Overflows", f"Max Callers Limit ({max_callers})", 
                         f"{step_prefix}Simultaneously hold {max_callers} active calls in {c_name}. Dial the {max_callers + 1} call.", 
                         f"The {max_callers + 1} call instantly executes Overflow -> {max_dest_name}.")
            else:
                add_case(f"{cat_prefix}Overflows", "Unlimited Queue Capacity", 
                         f"{step_prefix}Place multiple concurrent calls into {c_name}.", 
                         "No calls are rejected or overflowed due to capacity limits.")

            if zero_out_target != "None Configured":
                add_case(f"{cat_prefix}Overflows", "Zero-Out (DTMF Escape)", 
                         f"{step_prefix}While listening to {c_name} hold music, press '0'.", 
                         f"Call immediately escapes queue and routes to -> {zero_out_target}.")
            else:
                add_case(f"{cat_prefix}Overflows", "Zero-Out Disabled", 
                         f"{step_prefix}While listening to {c_name} hold music, press '0'.", 
                         "Input is ignored. Call remains in queue because no operator/recipient is configured.")

        # --- 4. IVR MENU ---
        elif c_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{c_id}', method='GET', raise_error=False) or {}
            
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    
                    target_name, target_id = resolve_target(act, ext_map)
                    
                    add_case(f"{cat_prefix}IVR Routing", f"Key Mapping: Press '{key}'", 
                             f"{step_prefix}Listen to prompt and press '{key}'.", 
                             f"System processes input and immediately routes -> {target_name}.")
                    
                    if target_id and target_id not in processed_ids and ext_map.get(target_id, {}).get('type') in ['Department', 'IvrMenu']:
                        nodes_to_process.append({"id": target_id, "name": ext_map[target_id]['name'], "ext": ext_map[target_id]['ext'], "type": ext_map[target_id]['type'], "context": f"Key '{key}' from {c_name}"})
            
            add_case(f"{cat_prefix}IVR Boundaries", "Invalid Key Press", 
                     f"{step_prefix}Press an unassigned key in {c_name} (e.g., '9' or '#').", 
                     "System plays an 'Invalid entry' error prompt and replays the menu.")
            
            add_case(f"{cat_prefix}IVR Boundaries", "Timeout (No Input)", 
                     f"{step_prefix}Listen to entire prompt in {c_name} and provide no input.", 
                     "System times out, replays menu, and eventually executes default timeout routing.")

    return uat_cases
