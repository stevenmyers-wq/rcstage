# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, Sites, and Shared Lines for testing. Excludes standard Users."""
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
    """Builds a global directory to translate raw IDs into readable 'Name (Ext)' strings."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    ext_map = {}
    if response and 'records' in response:
        for ext in response['records']:
            ext_map[str(ext['id'])] = f"{ext.get('name', 'Unknown')} (Ext {ext.get('extensionNumber', 'N/A')})"
    return ext_map

def get_direct_number(ext_id):
    """Forensically extracts the primary DID assigned to the flow."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    if response and 'records' in response:
        for record in response['records']:
            if record.get('usageType') == 'DirectNumber':
                return record.get('phoneNumber')
    return None

def get_business_hours_string(ext_id):
    """
    Extracts explicit schedules. If an extension inherits account-level hours,
    it gracefully falls back to query the Account Business Hours endpoint.
    """
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
    
    # Fallback to Account level if the extension has no custom schedule defined
    if not response or 'schedule' not in response:
        response = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
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

def resolve_target(action_obj, ext_map):
    """Resolves nested JSON targets into human-readable destinations."""
    if not action_obj: return "Unknown Destination"
    
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return ext_map[target_id]
        
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return ext_map[target_id]
        
    return "External Number or Unknown Target"

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Generates an exhaustive, holistic UAT matrix covering every configurable portal option."""
    uat_cases = []
    case_counter = 1
    
    # Pre-fetch all forensic parameters
    ext_map = build_extension_map()
    direct_number = get_direct_number(extension_id)
    business_hours_str = get_business_hours_string(extension_id)
    
    requires_voicemail_test = False

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

    # ==========================================
    # 1. CONNECTIVITY & ROUTING
    # ==========================================
    add_case("1. Base Connectivity", "Internal Net Dialing", 
             f"From an internal RingCentral device, dial extension {extension_number}.", 
             f"Call connects successfully to '{extension_name}' without SIP errors or dead air.")
    
    if direct_number:
        add_case("1. Base Connectivity", "PSTN Inbound Routing (DID)", 
                 f"From an external mobile device, dial the assigned DID: {direct_number}.", 
                 f"Call successfully traverses the PSTN and hits '{extension_name}'. Audio is two-way and high fidelity.")
    else:
        add_case("1. Base Connectivity", "PSTN Inbound Routing (Auto-Receptionist)", 
                 f"Dial the Main Company Number. When prompted by the greeting, enter extension {extension_number}.", 
                 f"Call successfully routes from the auto-receptionist to '{extension_name}'.")

    # ==========================================
    # 2. TIME OF DAY ROUTING
    # ==========================================
    add_case("2. Schedule Boundaries", "Business Hours Routing", 
             f"Initiate a test call during the configured Open Hours: [{business_hours_str}].", 
             "Call follows the primary 'Open' routing path (e.g., rings agents or plays open IVR).")
    
    if business_hours_str != "24/7 (Always Open)":
        add_case("2. Schedule Boundaries", "After Hours Routing", 
                 f"Initiate a test call OUTSIDE of the configured Open Hours: [{business_hours_str}].", 
                 "Call intercepts and follows the 'Closed/After Hours' routing path (e.g., plays After Hours greeting, routes to Voicemail).")

    # ==========================================
    # 3. CALL QUEUE (DEPARTMENT) EXHAUSTIVE MATRIX
    # ==========================================
    if extension_type == 'Department':
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False)
        
        if q_info:
            transfer_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15) 
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            
            hold_action = q_info.get('holdTimeExpirationAction', 'Unknown')
            max_call_action = q_info.get('maxCallersAction', 'Unknown')
            
            hold_dest_name = resolve_target(q_info.get('transfer') or q_info.get('voicemail'), ext_map)
            
            if hold_action == 'TakeMessagesReturnToGreeting' or max_call_action == 'TakeMessagesReturnToGreeting':
                requires_voicemail_test = True

            # --- Caller Experience (Audio & Prompts) ---
            add_case("3. Caller Experience", "Queue Greeting (Intro)", 
                     "Place a call to the queue.", 
                     "If configured, the Initial Queue Greeting plays fully before the call begins ringing agents or playing hold music.")
            add_case("3. Caller Experience", "Call Recording Announcement", 
                     "Place a call to the queue.", 
                     "If Automatic Call Recording is enabled, the 'This call is being recorded' prompt plays before connection.")
            add_case("3. Caller Experience", "Connecting Audio (Hold Music)", 
                     "Remain in the queue while agents are ringing/busy.", 
                     "The approved Hold Music or Custom Promotional Greeting plays cleanly without jitter.")
            add_case("3. Caller Experience", "Interrupt Audio (Periodic Announcements)", 
                     "Remain on hold in the queue for an extended period.", 
                     "If configured, periodic announcements (e.g., 'Please continue to hold' or Estimated Wait Time/Position) interrupt the hold music at the correct intervals.")

            # --- Agent State Management ---
            add_case("4. Agent States", "Queue Opt-In (Accept Calls)", 
                     f"Agent toggles 'Accept Queue Calls' to ON in the RC App. Place a test call.", 
                     f"Agent's device rings. The Queue Name '{extension_name}' is prepended to the Caller ID display so the agent knows how to answer.")
            add_case("4. Agent States", "Queue Opt-Out (DND/Offline)", 
                     "Agent toggles 'Accept Queue Calls' to OFF. Place a test call.", 
                     "Agent's device does NOT ring. Call seamlessly hunts to the next available agent.")
            add_case("4. Agent States", "Agent Busy (On another call)", 
                     "Agent answers a non-queue call. Place a call into the queue.", 
                     "The system recognizes the agent is busy. Depending on user settings, the queue call either hunts to the next agent or provides call waiting.")
            add_case("4. Agent States", "Wrap-Up (After Call Work - ACW)", 
                     "Agent answers a queue call, connects, and hangs up. Place a second call immediately into the queue.", 
                     "Agent enters 'Wrap-Up' status and does NOT receive the second call until their configured ACW timer expires.")

            # --- Distribution Logic ---
            add_case("5. Distribution Logic", "Active Call Decline", 
                     "While the queue call is ringing an agent, the agent actively presses 'Decline / Send to VM'.", 
                     "Ringing stops for that agent immediately. The call is NOT disconnected; it hunts to the next available agent.")

            if transfer_mode == 'Simultaneous':
                add_case("5. Distribution Logic", f"Mode: {transfer_mode}", 
                         "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                         "The call rings ALL available queue members at the exact same time.")
            else:
                add_case("5. Distribution Logic", f"Mode: {transfer_mode}", 
                         "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                         f"The call cascades to agents based on {transfer_mode} logic (e.g., longest idle or fixed order).")
                
                add_case("5. Distribution Logic", f"Agent Ring Timeout ({agent_timeout}s)", 
                         f"The targeted agent lets the call ring without answering or declining for exactly {agent_timeout} seconds.", 
                         f"The {agent_timeout}s timer expires. The call automatically drops from Agent 1 and begins ringing Agent 2.")

            # --- Queue Capacity & Overflows ---
            add_case("6. Queue Boundaries", "Zero Agents Logged In (No Members)", 
                     "Ensure ALL assigned agents are either Logged Out or set to 'Not Accepting Queue Calls'. Initiate a call.", 
                     "Call immediately bypasses queue hold music and triggers the 'No Members Available' overflow routing.")

            if hold_time > 0:
                add_case("6. Queue Boundaries", f"Max Wait Time Limit ({hold_time}s)", 
                         f"Remain on hold in the queue until the {hold_time} second limit is reached.", 
                         f"The {hold_time}s timer expires. Call is forcefully removed from the queue and executes: [{hold_action}] -> {hold_dest_name}.")
            
            if max_callers > 0:
                add_case("6. Queue Boundaries", f"Max Callers Limit ({max_callers} callers)", 
                         f"Simultaneously flood the queue with {max_callers + 1} concurrent inbound calls.", 
                         f"The final call breaches the maximum queue capacity of {max_callers}. It instantly bypasses hold music and executes: [{max_call_action}].")

            add_case("6. Queue Boundaries", "Zero-Out Exception (DTMF Interrupt)", 
                     "While listening to the queue hold music, press '0' on the dialpad.", 
                     "If a zero-out operator is configured, the call escapes the queue. If not configured, the DTMF input is gracefully ignored.")

    # ==========================================
    # 4. IVR MENU EXHAUSTIVE MATRIX
    # ==========================================
    elif extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info:
            prompt_data = ivr_info.get('prompt', {})
            prompt_desc = f"Text-to-Speech ('{prompt_data.get('text', '...')}')" if 'text' in prompt_data else "Uploaded Audio File"
            
            # --- Prompt Audio ---
            add_case("3. IVR Experience", "Audio Quality & Script Verification", 
                     "Dial the IVR menu and listen to the complete greeting.", 
                     f"The {prompt_desc} plays cleanly. The wording matches the officially approved script perfectly.")
            add_case("3. IVR Experience", "Barge-in (Interruptibility)", 
                     "Dial the IVR menu. While the greeting is still actively playing, press a valid menu key.", 
                     "The IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the rest of the greeting.")
            
            # --- Explicit Key Mappings ---
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    
                    act_type = act.get('action', 'Unknown')
                    if act_type == 'Transfer':
                        target_name = resolve_target(act, ext_map)
                        expected_str = f"System registers input and transfers the call to: [{target_name}]. Verify target rings."
                    elif act_type == 'TakeMessagesReturnToGreeting':
                        target_name = resolve_target(act, ext_map)
                        expected_str = f"System transfers the call directly to Voicemail of: [{target_name}]."
                        requires_voicemail_test = True
                    elif act_type == 'Forward':
                        expected_str = f"System unconditionally forwards to external number: {act.get('phoneNumber', 'Unknown')}. Verify caller ID pass-through."
                    else:
                        expected_str = f"System triggers {act_type} logic."
                    
                    add_case("4. IVR Key Routing", f"Valid Input: Press '{key}'", 
                             f"Listen to the IVR prompt and press '{key}' on the dialpad.", 
                             expected_str)
            
            # --- Boundaries ---
            add_case("5. IVR Boundaries", "Multi-Digit Extension Dialing", 
                     "While in the IVR, dial a known 3 or 4-digit internal extension number using the dialpad.", 
                     "If general extension dialing is enabled, the IVR intercepts the string and transfers the caller directly to that extension.")
            add_case("5. IVR Boundaries", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "The system plays an 'Invalid entry' error prompt and seamlessly replays the main menu from the beginning.")
            add_case("5. IVR Boundaries", "Timeout (No Input Provided)", 
                     "Listen to the entire IVR prompt and provide no DTMF input.", 
                     "The system times out. It replays the menu (usually 3 times) and then executes the default timeout routing (e.g. disconnect or Operator).")

    # ==========================================
    # 5. POST-CALL & INFRASTRUCTURE
    # ==========================================
    
    if requires_voicemail_test or extension_type == 'Department':
        add_case("7. Voicemail Check", "Voicemail Deposit Verification", 
                 "Trigger an overflow or key press that routes to Voicemail. Leave a 10-second test message.", 
                 "The correct Voicemail greeting plays. The 10-second test message is successfully recorded without being cut off.")
        add_case("7. Voicemail Check", "Voicemail Delivery & Transcription", 
                 "Check the designated target's inbox (Email notification or RingEX App).", 
                 "The voicemail audio file is delivered accurately. If enabled, the Voice-to-Text transcription is included.")

    add_case("8. Analytics", "Call Log Generation", 
             "Verify the test calls in the RingCentral Analytics or Call Log portal.", 
             "The system generates accurate Call Log data containing the correct Caller ID, duration, and routing path.")
             
    add_case("8. Analytics", "Clean Disconnect", 
             "During any active connected state, have the caller hang up.", 
             "The call drops immediately across all endpoints. Queue agents are returned to 'Available' status.")

    return uat_cases
