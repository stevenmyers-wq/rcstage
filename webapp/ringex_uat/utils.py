# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, Sites, and Shared Lines for testing. EXCLUDES standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site', 'ParkLocation']
    
    entities = [
        {
            "id": ext['id'],
            "name": ext.get('name', 'Unnamed'),
            "extensionNumber": ext.get('extensionNumber', 'N/A'),
            "type": ext['type']
        }
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    
    return sorted(entities, key=lambda x: x['name'])

def build_extension_map():
    """Builds a dictionary to translate raw Extension IDs into readable Names & Numbers."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    ext_map = {}
    if response and 'records' in response:
        for ext in response['records']:
            ext_map[str(ext['id'])] = f"{ext.get('name', 'Unknown')} (Ext {ext.get('extensionNumber', 'N/A')})"
    return ext_map

def format_action_string(action, rule, ext_map):
    """Translates generic actions into specific destination names."""
    if action == 'TransferToExtension':
        target_id = str(rule.get('transfer', {}).get('extension', {}).get('id', ''))
        target_name = ext_map.get(target_id, f"Unknown ID {target_id}")
        return f"Call transfers internally to: {target_name}."
    elif action == 'TakeMessagesReturnToGreeting':
        target_id = str(rule.get('voicemail', {}).get('recipient', {}).get('id', ''))
        target_name = ext_map.get(target_id, f"Unknown ID {target_id}")
        return f"Call goes to Voicemail of: {target_name}. Verify correct VM greeting plays."
    elif action == 'PlayAnnouncementOnly':
        return "System plays a disconnect announcement and hangs up."
    elif action == 'UnconditionalForwarding':
        target_num = rule.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown')
        return f"Call is unconditionally forwarded to external number: {target_num}."
    elif action == 'TransferToExternalNumber':
        return "Call transfers to an external phone number."
    elif action == 'ForwardCalls':
        return "Call routes to configured ringing members."
    elif action == 'Bypass':
        return "Call bypasses normal routing (usually goes direct to Voicemail)."
    return f"Call follows routing behavior: {action}."

def format_overflow_action(action, queue_obj, ext_map):
    """Specific parser for Queue Overflow actions."""
    if action == 'TransferToExtension':
        target_id = str(queue_obj.get('transfer', {}).get('extension', {}).get('id', ''))
        return f"Transfers to {ext_map.get(target_id, target_id)}"
    elif action == 'TakeMessagesReturnToGreeting':
        target_id = str(queue_obj.get('voicemail', {}).get('recipient', {}).get('id', ''))
        return f"Voicemail of {ext_map.get(target_id, target_id)}"
    elif action == 'UnconditionalForwarding':
        return f"Forwards to external number"
    return action

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Crawls routing data and generates exhaustive UAT test cases based on ACTUAL parameters."""
    uat_cases = []
    case_counter = 1
    
    # Pre-fetch the extension map to resolve IDs to Names
    ext_map = build_extension_map()

    def add_case(category, scenario, action, expected):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{extension_number}-{case_counter:03d}",
            "category": category,
            "scenario": scenario,
            "action": action,
            "expected": expected
        })
        case_counter += 1

    # --- 1. GENERAL CONNECTIVITY ---
    add_case("Connectivity", "Internal Routing", 
             f"Dial extension {extension_number} from an internal device.", 
             f"Call connects successfully to {extension_name} without dead air.")

    # --- 2. ANSWERING RULES & QUEUE DETAILS ---
    rules_response = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=False)
    
    if rules_response and 'records' in rules_response:
        for rule in rules_response['records']:
            if not rule.get('enabled', False):
                continue
                
            rule_type = rule.get('type')
            action = rule.get('callHandlingAction', 'Unknown')
            expected = format_action_string(action, rule, ext_map)

            # Custom Rules
            if rule_type == 'Custom':
                name = rule.get('name', 'Custom Rule')
                add_case("Time of Day / Custom", f"Rule: {name}", f"Initiate call matching the specific triggers of rule '{name}'.", expected)
            
            # After Hours Rules
            elif rule_type == 'AfterHours':
                add_case("Time of Day / Custom", "After Hours Routing", "Initiate call outside of configured Business Hours.", expected)
            
            # Business Hours Rules (And deep queue logic if it's a Department)
            elif rule_type == 'BusinessHours':
                if extension_type == 'Department' and action == 'ForwardCalls':
                    queue = rule.get('queue', {})
                    
                    # Fetch exact queue parameters
                    transfer_mode = queue.get('transferMode', 'Unknown')
                    hold_time = queue.get('holdTime', 'Unknown')
                    hold_action = queue.get('holdTimeExpirationAction', 'Unknown')
                    max_callers = queue.get('maxCallers', 'Unknown')
                    max_callers_action = queue.get('maxCallersAction', 'Unknown')
                    
                    hold_dest = format_overflow_action(hold_action, queue, ext_map)
                    max_dest = format_overflow_action(max_callers_action, queue, ext_map)

                    add_case("Queue Parameters", f"Distribution: {transfer_mode}", 
                             "Initiate call with multiple agents available in 'Accept Queue Calls' status.", 
                             f"Call rings agents according to '{transfer_mode}' logic.")
                    
                    if hold_time != 'Unknown':
                        add_case("Queue Boundaries", f"Max Wait Time ({hold_time} sec)", 
                                 f"Call queue and remain on hold for greater than {hold_time} seconds.", 
                                 f"Wait time expires. Call executes overflow action: {hold_dest}.")
                    
                    if max_callers != 'Unknown':
                        add_case("Queue Boundaries", f"Max Callers Limit ({max_callers})", 
                                 f"Simultaneously flood the queue with {max_callers + 1} concurrent inbound test calls.", 
                                 f"The final call breaches the limit and instantly executes overflow action: {max_dest}.")
                        
                    add_case("Queue Boundaries", "Zero Agents Logged In", 
                             "Ensure ALL queue members are set to 'Do Not Accept Queue Calls'. Initiate call.", 
                             "Call immediately bypasses queue hold music and triggers the 'No Members Available' routing.")
                else:
                    # Standard User/Site Business Hours
                    add_case("Time of Day / Custom", "Business Hours Routing", "Initiate call during configured Business Hours.", expected)

    # --- 3. IVR MENU DETAILS ---
    if extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/account/~/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info:
            # Parse the exact prompt
            prompt_data = ivr_info.get('prompt', {})
            prompt_text = prompt_data.get('text', '')
            prompt_name = prompt_data.get('name', 'Audio File')
            
            prompt_desc = f"Text-To-Speech: '{prompt_text}'" if prompt_text else f"Audio File: '{prompt_name}'"
            
            add_case("IVR General", "Greeting Prompt Audio", 
                     "Call the IVR.", 
                     f"System plays prompt ({prompt_desc}). Verify audio matches approved script and plays cleanly.")
            
            # Parse exact key actions
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: 
                        continue # Skip internal config actions
                    
                    act_type = act.get('action', 'Unknown')
                    if act_type == 'Transfer':
                        target_id = str(act.get('extension', {}).get('id', ''))
                        target_name = ext_map.get(target_id, f"Unknown ID {target_id}")
                        expected_str = f"Call successfully transfers to {target_name}."
                    elif act_type == 'Forward':
                        expected_str = f"Call forwards to external number: {act.get('phoneNumber', 'Unknown')}."
                    else:
                        expected_str = f"System triggers {act_type} logic."
                    
                    add_case("IVR Key Mapping", f"Valid Input: '{key}'", f"Listen to prompt and press '{key}' on dialpad.", expected_str)
                
            # Standard IVR boundaries
            add_case("IVR Boundaries", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "System plays 'Invalid entry' prompt and replays the menu from the beginning.")
            add_case("IVR Boundaries", "Timeout (No Input)", 
                     "Listen to the IVR prompt and provide no input.", 
                     "System times out and executes the default timeout action (usually loops 3 times then disconnects/transfers).")

    # --- 4. WRAP UP ---
    add_case("Termination", "Clean Disconnect", 
             "During an active connected state, the caller hangs up.", 
             "Call drops immediately. Agents are returned to 'Available' status and accurate call logs are generated.")

    return uat_cases
