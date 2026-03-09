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

def get_direct_number(ext_id):
    """Fetches the primary direct phone number (DID) for an extension."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    if response and 'records' in response:
        for record in response['records']:
            if record.get('usageType') == 'DirectNumber':
                return record.get('phoneNumber')
    return None

def get_business_hours_string(ext_id):
    """Fetches and formats the specific business hours schedule."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
    if not response or 'schedule' not in response:
        return "24/7 (Always Open)"
    
    schedule = response['schedule']
    if 'weeklyRanges' in schedule:
        days = []
        for day, times in schedule['weeklyRanges'].items():
            if times:
                time_str = f"{times[0].get('from', '')}-{times[0].get('to', '')}"
                days.append(f"{day[:3]} {time_str}")
        return ", ".join(days) if days else "Custom Schedule"
    return "Custom/Specific Schedule"

def format_overflow_action(action, config_obj, ext_map):
    """Specific parser for resolving overflow destination names."""
    if action == 'TransferToExtension':
        target_id = str(config_obj.get('transfer', {}).get('extension', {}).get('id', ''))
        return f"transfers to {ext_map.get(target_id, target_id)}"
    elif action == 'TakeMessagesReturnToGreeting':
        target_id = str(config_obj.get('voicemail', {}).get('recipient', {}).get('id', ''))
        return f"routes to Voicemail of {ext_map.get(target_id, target_id)}"
    elif action == 'UnconditionalForwarding':
        return "forwards to external number"
    elif action == 'PlayAnnouncementOnly':
        return "plays an announcement and disconnects"
    return f"executes {action}"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Generates an exhaustive, parameter-driven UAT script."""
    uat_cases = []
    case_counter = 1
    
    # 1. Pre-fetch all necessary specific parameters
    ext_map = build_extension_map()
    direct_number = get_direct_number(extension_id)
    business_hours_str = get_business_hours_string(extension_id)

    def add_case(category, test_name, step, expected):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{extension_number}-{case_counter:03d}",
            "category": category,
            "scenario": test_name,
            "action": step,
            "expected": expected
        })
        case_counter += 1

    # --- 1. BASE CONNECTIVITY (Dynamic based on DID) ---
    add_case("1. Connectivity", "Internal Dialing", 
             f"Dial extension {extension_number} from an internal RingEX app or deskphone.", 
             f"Call connects successfully to {extension_name} without dead air or SIP errors.")
    
    if direct_number:
        add_case("1. Connectivity", "External Dialing", 
                 f"Dial the external Direct Inward Dialing (DID) number {direct_number} from a mobile phone.", 
                 f"Call connects via the PSTN with high-quality, two-way audio.")
    else:
        add_case("1. Connectivity", "External Dialing (No DID)", 
                 f"Dial the Main Company Number, and when prompted, enter extension {extension_number}.", 
                 f"Call routes from the auto-receptionist and connects successfully.")

    # --- 2. EXHAUSTIVE CALL QUEUE TESTS (Highly Parameterized) ---
    if extension_type == 'Department':
        # Fetch deep queue parameters
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False)
        if q_info:
            transfer_mode = q_info.get('transferMode', 'Unknown')
            agent_timeout = q_info.get('agentTimeout', 0)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            
            hold_action = q_info.get('holdTimeExpirationAction', 'Unknown')
            max_callers_action = q_info.get('maxCallersAction', 'Unknown')
            
            hold_dest = format_overflow_action(hold_action, q_info, ext_map)
            max_dest = format_overflow_action(max_callers_action, q_info, ext_map)

            # Agent Experience based on Transfer Mode
            add_case("2. Agent Experience", "Agent Login / Accept Calls", 
                     "Have a queue agent toggle 'Accept Queue Calls' to ON in the RingEX App. Place a test call.", 
                     f"The agent's device rings with the incoming queue call, displaying '{extension_name}' on the caller ID.")
            
            add_case("2. Agent Experience", "Active Call Decline", 
                     "Agent actively presses 'Decline' on the incoming queue call.", 
                     "The call immediately stops ringing that agent and hunts to the next available agent (or continues ringing others if Simultaneous).")

            if transfer_mode == 'Simultaneous':
                add_case("3. Queue Routing", f"Distribution Method: {transfer_mode}", 
                         "Ensure multiple agents are available. Place a call into the queue.", 
                         "The call rings ALL available agents simultaneously.")
            else:
                add_case("3. Queue Routing", f"Distribution Method: {transfer_mode}", 
                         "Ensure multiple agents are available. Place a call into the queue.", 
                         f"The call rings a single agent according to {transfer_mode} logic.")
                
                # Only test Agent Ring Timeout if it's NOT simultaneous
                add_case("3. Queue Routing", f"Agent Ring Timeout ({agent_timeout}s)", 
                         f"Agent lets the call ring without answering or declining for {agent_timeout} seconds.", 
                         f"The call automatically stops ringing the first agent and moves to the next available agent.")

            # Hard Boundaries based on exact numbers
            add_case("4. Queue Boundaries", "Hold Music Verification", 
                     "Call the queue and remain on hold.", 
                     "The officially approved Hold Music or custom promotional messaging plays cleanly without distortion.")
            
            if hold_time > 0:
                add_case("4. Queue Boundaries", f"Max Wait Time Overflow ({hold_time}s)", 
                         f"Call the queue and remain on hold until the {hold_time} second timer expires.", 
                         f"The call is automatically removed from the queue and {hold_dest}.")
            
            if max_callers > 0:
                add_case("4. Queue Boundaries", f"Max Callers Limit ({max_callers})", 
                         f"Simultaneously flood the queue with {max_callers + 1} concurrent test calls.", 
                         f"The final call breaches the capacity limit of {max_callers} and instantly {max_dest} without playing hold music.")
            
            add_case("4. Queue Boundaries", "Zero Agents Available Overflow", 
                     "Ensure ALL assigned agents are logged out or on DND. Initiate a call to the queue.", 
                     "The call immediately bypasses the queue and follows 'No Members Available' routing rules.")

    # --- 3. EXHAUSTIVE IVR MENU TESTS (Highly Parameterized) ---
    elif extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info:
            prompt_data = ivr_info.get('prompt', {})
            prompt_desc = "Text-to-Speech prompt" if 'text' in prompt_data else "Uploaded Audio prompt"
            
            add_case("2. IVR Prompts", "Audio Quality & Script", 
                     "Dial the IVR menu.", 
                     f"The {prompt_desc} plays cleanly. The wording matches the officially approved script exactly.")
            add_case("2. IVR Prompts", "Barge-in (Interrupt)", 
                     "Dial the IVR menu. While the greeting is still playing, press a valid menu key.", 
                     "The IVR accepts the input immediately without forcing the caller to listen to the entire message.")
            
            # Map exact keys
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: 
                        continue 
                    
                    act_type = act.get('action', 'Unknown')
                    if act_type == 'Transfer':
                        target_id = str(act.get('extension', {}).get('id', ''))
                        expected_str = f"Call successfully transfers to {ext_map.get(target_id, target_id)}."
                    elif act_type == 'Forward':
                        expected_str = f"Call forwards to external number: {act.get('phoneNumber', 'Unknown')}."
                    else:
                        expected_str = f"System triggers {act_type} logic."
                    
                    add_case("3. IVR Navigation", f"Valid Key Press: '{key}'", f"Listen to prompt and press '{key}' on dialpad.", expected_str)
            
            add_case("4. IVR Boundaries", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "The system plays an 'Invalid entry' prompt and replays the main menu.")
            add_case("4. IVR Boundaries", "Timeout (No Input)", 
                     "Listen to the entire IVR prompt and provide no DTMF input.", 
                     "The system times out. It replays the menu and eventually executes the default timeout routing.")

    # --- 4. TIME OF DAY (Dynamic Schedule) ---
    add_case("5. Time of Day", "Business Hours", 
             f"Initiate a call during configured Open Hours: {business_hours_str}.", 
             "Call follows standard Business Hours routing.")
    add_case("5. Time of Day", "After Hours", 
             f"Initiate a call outside of configured Business Hours ({business_hours_str}).", 
             "Call follows After Hours routing (e.g., plays closed greeting and routes to Voicemail).")

    # --- 5. TERMINATION ---
    add_case("6. Termination", "Clean Disconnect", 
             "During an active connected state, the caller hangs up.", 
             "The call drops immediately. RingCentral generates accurate call logs.")

    return uat_cases
