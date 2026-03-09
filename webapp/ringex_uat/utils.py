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
        return f"transfers to {ext_map.get(target_id, f'Extension ID {target_id}')}"
    elif action == 'TakeMessagesReturnToGreeting':
        target_id = str(config_obj.get('voicemail', {}).get('recipient', {}).get('id', ''))
        return f"routes to Voicemail of {ext_map.get(target_id, f'Extension ID {target_id}')}"
    elif action == 'UnconditionalForwarding':
        num = config_obj.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown Number')
        return f"forwards to external number ({num})"
    elif action == 'PlayAnnouncementOnly':
        return "plays an announcement and disconnects"
    return f"executes {action}"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Generates an exhaustive, parameter-driven UAT script covering boundaries and overflows."""
    uat_cases = []
    case_counter = 1
    
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

    # --- 1. BASE CONNECTIVITY ---
    add_case("1. Connectivity", "Internal Dialing", 
             f"Dial extension {extension_number} from an internal RingEX app or deskphone.", 
             f"Call connects successfully to {extension_name} without dead air or SIP errors.")
    
    if direct_number:
        add_case("1. Connectivity", "External Dialing (DID)", 
                 f"Dial the external Direct Inward Dialing (DID) number {direct_number} from a mobile phone.", 
                 f"Call connects via the PSTN with high-quality, two-way audio.")
    else:
        add_case("1. Connectivity", "External Dialing (Auto-Receptionist)", 
                 f"Dial the Main Company Number. When prompted, enter extension {extension_number}.", 
                 f"Call successfully routes from the auto-receptionist to {extension_name}.")

    # --- 2. EXHAUSTIVE CALL QUEUE TESTS ---
    if extension_type == 'Department':
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

            # Agent Experience Matrix
            add_case("2. Agent Experience", "Agent Login / Accept Calls", 
                     "Have a queue agent toggle 'Accept Queue Calls' to ON in the RingEX App. Place a test call.", 
                     f"The agent's device rings. Caller ID displays queue name '{extension_name}'.")
            
            add_case("2. Agent Experience", "Agent Logout / DND", 
                     "Have the queue agent toggle 'Accept Queue Calls' to OFF. Place a test call.", 
                     "The agent's device does NOT ring. Call seamlessly hunts to the next available agent.")
            
            add_case("2. Agent Experience", "Active Call Decline", 
                     "Agent actively presses 'Decline' on the incoming queue call.", 
                     "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller.")

            # Distribution Logic
            if transfer_mode == 'Simultaneous':
                add_case("3. Queue Routing", f"Distribution Method: {transfer_mode}", 
                         "Ensure multiple agents are 'Available'. Place a call into the queue.", 
                         "The call rings ALL available agents simultaneously.")
            else:
                add_case("3. Queue Routing", f"Distribution Method: {transfer_mode}", 
                         "Ensure multiple agents are 'Available'. Place a call into the queue.", 
                         f"The call rings a single agent according to {transfer_mode} logic.")
                
                # Ring timeout ONLY applies if it's not simultaneous
                if agent_timeout > 0:
                    add_case("3. Queue Routing", f"Missed Call (Ring Timeout - {agent_timeout}s)", 
                             f"Agent lets the call ring without answering or declining for {agent_timeout} seconds.", 
                             "The call automatically stops ringing the first agent and moves to the next available agent.")

            add_case("3. Queue Routing", "After Call Work (ACW) / Wrap-up", 
                     "Agent answers a queue call and hangs up. Place another call immediately into the queue.", 
                     "Agent enters Wrap-Up status and does not receive the new call until the configured ACW timer expires.")

            # Hard Boundaries & Overflows
            add_case("4. Queue Boundaries", "Hold Music Verification", 
                     "Call the queue and remain on hold.", 
                     "The officially approved Hold Music or Custom Promotional Greeting plays cleanly.")
            
            if hold_time > 0:
                add_case("4. Queue Boundaries", f"Max Wait Time Overflow ({hold_time}s)", 
                         f"Call the queue and remain on hold until the {hold_time} second timer expires.", 
                         f"The call is removed from the queue and {hold_dest}. Verify target destination behaves correctly.")
            
            if max_callers > 0:
                add_case("4. Queue Boundaries", f"Max Callers Limit ({max_callers})", 
                         f"Simultaneously flood the queue with {max_callers + 1} concurrent inbound calls.", 
                         f"The final call breaches the capacity limit of {max_callers} and instantly {max_dest} without playing hold music.")
            
            add_case("4. Queue Boundaries", "Zero Agents Available Overflow", 
                     "Ensure ALL assigned agents are logged out or on DND. Initiate a call to the queue.", 
                     "The call immediately bypasses the queue and follows 'No Members Available' routing rules.")
            
            add_case("4. Queue Boundaries", "Queue Zero-Out Exception", 
                     "While listening to queue hold music, press '0' on the dialpad.", 
                     "If configured, the call escapes the queue and routes to the designated operator. Otherwise, the DTMF input is ignored gracefully.")

            # Voicemail Verification (Triggered if any overflow destination relies on Voicemail)
            if 'Voicemail' in hold_dest or 'Voicemail' in max_dest:
                add_case("5. Voicemail Check", "Voicemail Deposit (Overflow)", 
                         "Trigger a queue overflow that routes to Voicemail. Leave a 10-second test message.", 
                         "The correct Voicemail greeting plays. The test message is successfully recorded.")
                add_case("5. Voicemail Check", "Voicemail Delivery (Overflow)", 
                         "Check the designated Voicemail recipient's inbox (Email or RingEX App).", 
                         "The voicemail audio file and transcript are delivered accurately.")

    # --- 3. EXHAUSTIVE IVR MENU TESTS ---
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
            
            # Map exact keys and their specific destinations
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: 
                        continue 
                    
                    act_type = act.get('action', 'Unknown')
                    if act_type == 'Transfer':
                        target_id = str(act.get('extension', {}).get('id', ''))
                        target_name = ext_map.get(target_id, target_id)
                        expected_str = f"Call successfully transfers to {target_name}. Verify target destination connects."
                    elif act_type == 'Forward':
                        expected_str = f"Call forwards to external number: {act.get('phoneNumber', 'Unknown')}. Verify caller ID passes through correctly."
                    else:
                        expected_str = f"System triggers {act_type} logic."
                    
                    add_case("3. IVR Navigation", f"Valid Key Press: '{key}'", f"Listen to prompt and press '{key}' on dialpad.", expected_str)
            
            add_case("3. IVR Navigation", "Multi-Digit Extension Dialing", 
                     "While in the IVR, dial a known 3 or 4-digit internal extension number.", 
                     "If enabled, the IVR intercepts the dial string and transfers the caller directly to that internal extension.")
            
            add_case("4. IVR Boundaries", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '*').", 
                     "The system plays an 'Invalid entry' prompt and replays the main menu.")
            add_case("4. IVR Boundaries", "Timeout (No Input)", 
                     "Listen to the entire IVR prompt and provide no DTMF input.", 
                     "The system times out. It replays the menu and eventually executes the default timeout routing (e.g. Operator transfer).")

    # --- 4. TIME OF DAY (Dynamic Schedule for all types) ---
    add_case("6. Time of Day", "Business Hours Routing", 
             f"Initiate a call during configured Open Hours: {business_hours_str}.", 
             "Call follows standard Business Hours routing path.")
    add_case("6. Time of Day", "After Hours Routing", 
             f"Initiate a call outside of configured Business Hours ({business_hours_str}).", 
             "Call follows After Hours routing (e.g., plays closed greeting, routes to After Hours IVR or Voicemail).")
    add_case("6. Time of Day", "Holiday Routing", 
             "Initiate a call during a pre-configured Holiday schedule.", 
             "Call follows Holiday routing, overriding Business/After hours logic.")

    # --- 5. TERMINATION ---
    add_case("7. Termination", "Clean Disconnect", 
             "During any active connected state, the caller hangs up.", 
             "The call drops immediately. RingCentral generates accurate call logs and agents return to 'Available' status.")

    return uat_cases
