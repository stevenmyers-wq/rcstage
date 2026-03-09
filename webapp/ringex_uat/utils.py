# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, Sites, and Shared Lines for testing. EXCLUDES standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    # Exclude standard Users to focus purely on Call Flows
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

def format_action_string(action, rule):
    """Translates raw API actions into human-readable UAT expected results."""
    if action == 'TransferToExtension':
        target_ext = rule.get('transfer', {}).get('extension', {}).get('id', 'Unknown')
        return f"Call transfers internally to Extension ID {target_ext}."
    elif action == 'TakeMessagesReturnToGreeting':
        vm_recipient = rule.get('voicemail', {}).get('recipient', {}).get('id', 'this extension')
        return f"Call goes to Voicemail (Recipient ID: {vm_recipient}). Verify correct greeting plays."
    elif action == 'PlayAnnouncementOnly':
        return "System plays an announcement and automatically disconnects the call."
    elif action == 'UnconditionalForwarding':
        target_num = rule.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown')
        return f"Call is unconditionally forwarded to external number: {target_num}."
    elif action == 'TransferToExternalNumber':
        return "Call transfers to an external phone number."
    elif action == 'ForwardCalls':
        return "Call routes to configured ringing members based on distribution method."
    elif action == 'Bypass':
        return "Call bypasses normal routing (usually direct to Voicemail)."
    return f"Call follows routing behavior: {action}."

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Crawls routing data and generates exhaustive UAT test cases."""
    uat_cases = []
    case_counter = 1

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

    # --- 1. GENERAL CONNECTIVITY (ALL TYPES) ---
    add_case("Connectivity", "Internal Routing", 
             f"Dial extension {extension_number} from an internal deskphone or softphone.", 
             "Call connects successfully to the target without SIP errors or dead air.")
    add_case("Connectivity", "External Routing", 
             f"Dial the external Direct Inward Dialing (DID) number mapped to {extension_name}.", 
             "Call routes from the PSTN and connects to the target successfully.")

    # --- 2. CRAWL ANSWERING RULES (TIME OF DAY & ROUTING) ---
    rules_response = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=False)
    
    if rules_response and 'records' in rules_response:
        for rule in rules_response['records']:
            if not rule.get('enabled', False):
                continue
                
            rule_type = rule.get('type')
            action = rule.get('callHandlingAction', 'Unknown')
            expected = format_action_string(action, rule)

            if rule_type == 'Custom':
                name = rule.get('name', 'Custom Rule')
                add_case("Time of Day / Custom", f"Trigger: {name}", f"Initiate call matching the custom parameters of rule '{name}'.", expected)
            elif rule_type == 'BusinessHours':
                add_case("Time of Day / Custom", "Business Hours Routing", "Initiate call during configured Business Hours.", expected)
            elif rule_type == 'AfterHours':
                add_case("Time of Day / Custom", "After Hours Routing", "Initiate call outside of configured Business Hours.", expected)

    # --- 3. EXHAUSTIVE CALL QUEUE (DEPARTMENT) TESTS ---
    if extension_type == 'Department':
        # Agent Experience
        add_case("Agent Experience", "Queue Login / Logout Toggle", 
                 "Queue member toggles 'Accept Queue Calls' to OFF in their RC App.", 
                 "Agent does not receive queue calls. Queue routing correctly skips them.")
        add_case("Agent Experience", "Call Decline / Wrap-up", 
                 "Queue member actively declines an incoming queue call.", 
                 "Call immediately hunts to the next available agent without waiting for the full ring duration.")
        add_case("Agent Experience", "Queue Distribution Check", 
                 "Initiate a test call into the queue with multiple agents available.", 
                 "Call rings agents according to the specific configured distribution method (e.g., Rotating, Simultaneous).")
        
        # Overflows and Boundaries (The real UAT stuff)
        add_case("Queue Boundaries", "Max Wait Time Reached", 
                 "Call queue and remain on hold until the configured maximum wait time is exceeded.", 
                 "Call is removed from hold and successfully follows the Primary Overflow action (e.g., Voicemail).")
        add_case("Queue Boundaries", "Zero Agents Logged In", 
                 "Ensure ALL queue members are logged out or set to 'Do Not Accept Queue Calls'. Initiate call.", 
                 "Call immediately bypasses the queue and follows 'No Members Available' routing rules without playing hold music.")
        add_case("Queue Boundaries", "Max Callers in Queue", 
                 "Simultaneously flood the queue with inbound test calls up to the maximum queue capacity.", 
                 "The call that breaches the limit instantly triggers the 'Max Callers' overflow action.")
        add_case("Queue Boundaries", "Queue Zero-Out Exception", 
                 "Call queue. While listening to the initial greeting or hold music, press '0' on the dialpad.", 
                 "If configured, call escapes the queue and routes to the designated operator. Otherwise, input is ignored gracefully.")

    # --- 4. EXHAUSTIVE IVR MENU TESTS ---
    if extension_type == 'IvrMenu':
        # General IVR Audio
        add_case("IVR General", "Audio Prompt Quality", 
                 "Call the IVR.", 
                 "The prompt audio file plays cleanly, without distortion, and matches the approved script.")
        
        # Crawl Specific Keys
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info and 'actions' in ivr_info:
            for action in ivr_info['actions']:
                key = action.get('input', 'Unknown')
                act_type = action.get('action', 'Unknown')
                target = action.get('extension', {}).get('id', 'Unknown') if act_type == 'Transfer' else 'N/A'
                
                expected_str = f"Call successfully transfers to Extension ID {target}." if act_type == 'Transfer' else f"Triggers {act_type} logic."
                add_case("IVR Key Mapping", f"Valid Input: Press '{key}'", f"Listen to prompt and press '{key}' on dialpad.", expected_str)
            
            # Boundary conditions
            add_case("IVR Constraints", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "System plays 'Invalid entry' prompt and replays the menu from the beginning.")
            add_case("IVR Constraints", "Timeout (No Input)", 
                     "Listen to the IVR prompt and provide no input.", 
                     "System times out (typically after 3 loops) and executes the default timeout action (disconnect or operator transfer).")

    # --- 5. WRAP UP ---
    add_case("Termination", "Clean Disconnect", 
             "During an active connected state, the caller hangs up.", 
             "Call drops immediately from the RingCentral system. Queue agents are returned to an 'Available' status.")

    return uat_cases
