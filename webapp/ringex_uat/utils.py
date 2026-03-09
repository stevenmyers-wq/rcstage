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
            ext_map[str(ext['id'])] = f"{ext.get('name', 'Unknown')} (Ext {ext.get('extensionNumber', 'N/A')})"
    return ext_map

def get_direct_numbers(ext_id):
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = []
    if response and 'records' in response:
        for record in response['records']:
            if record.get('usageType') == 'DirectNumber':
                numbers.append(record.get('phoneNumber'))
    return ", ".join(numbers) if numbers else None

def get_business_hours_string(ext_id):
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/business-hours', method='GET', raise_error=False)
    if not response or 'schedule' not in response:
        response = rc_api_call('/restapi/v1.0/account/~/business-hours', method='GET', raise_error=False)
        if not response or 'schedule' not in response:
            return "24/7 (Always Open)"
    
    schedule = response['schedule']
    if 'weeklyRanges' in schedule:
        days = []
        for day, times in schedule['weeklyRanges'].items():
            if times:
                days.append(f"{day[:3]} {times[0].get('from', '')}-{times[0].get('to', '')}")
        return ", ".join(days) if days else "Custom Schedule"
    return "Custom/Specific Schedule"

def resolve_target(action_obj, ext_map):
    if not action_obj: return "Unknown Destination"
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map: return ext_map[target_id]
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"Voicemail of {ext_map[target_id]}"
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}"
    return "Default Target"

def get_queue_greetings(ext_id):
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting', method='GET', raise_error=False)
    greetings = {'intro': False, 'audio_while_connecting': False, 'voicemail': False}
    if response and 'records' in response:
        for g in response['records']:
            if g.get('type') == 'Introductory' and g.get('preset') != 'Default': greetings['intro'] = True
            if g.get('type') == 'ConnectingAudio': greetings['audio_while_connecting'] = True
            if g.get('type') == 'Voicemail': greetings['voicemail'] = True
    return greetings

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    uat_cases = []
    case_counter = 1
    
    ext_map = build_extension_map()
    did_numbers = get_direct_numbers(extension_id)
    bh_string = get_business_hours_string(extension_id)

    def add_case(category, scenario, step, expected):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{extension_number}-{case_counter:03d}",
            "category": category,
            "scenario": scenario,
            "action": step,
            "expected": expected
        })
        case_counter += 1

    # ==========================================
    # CATEGORY: INTEGRATION
    # ==========================================
    add_case("Integration", "Internal Dialing", 
             f"Dial extension {extension_number} from an internal RingCentral app or deskphone.", 
             f"Call connects successfully to {extension_name} without dead air or SIP errors.")
    
    if did_numbers:
        add_case("Integration", "External Dialing (DID)", 
                 f"Dial the external Direct Inward Dialing (DID) number assigned to {extension_name} ({did_numbers}) from a mobile phone.", 
                 "Call connects via the PSTN with high-quality, two-way audio.")
    else:
        add_case("Integration", "External Dialing (Auto-Receptionist)", 
                 f"Dial the Main Company Number. When prompted by the auto-receptionist, enter extension {extension_number}.", 
                 f"Call successfully routes through the auto-receptionist to {extension_name}.")

    add_case("Integration", "Call Quality Verification", 
             "Maintain an active connected call with the target for at least 2 minutes.", 
             "Audio remains high-fidelity, two-way, with no noticeable jitter, latency, or packet loss.")

    # ==========================================
    # CATEGORY: BUSINESS HOURS
    # ==========================================
    add_case("Business Hours", "In-Hours Routing", 
             f"Initiate a test call during the configured Open Hours: [{bh_string}].", 
             "Call follows the primary 'Open' routing rules (e.g., enters queue or IVR menu).")
    
    if bh_string != "24/7 (Always Open)":
        add_case("Business Hours", "Out-of-Hours Routing", 
                 f"Initiate a test call outside of the configured Open Hours: [{bh_string}].", 
                 "Call intercepts and follows 'Closed' routing (e.g., plays After Hours greeting or routes to Voicemail).")

    # ==========================================
    # CATEGORY: CALL QUEUE TESTS
    # ==========================================
    if extension_type == 'Department':
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False)
        greetings = get_queue_greetings(extension_id)
        
        transfer_mode = q_info.get('transferMode', 'Simultaneous') if q_info else 'Simultaneous'
        agent_timeout = q_info.get('agentTimeout', 15) if q_info else 15
        wrap_up_time = q_info.get('wrapUpTime', 0) if q_info else 0
        hold_time = q_info.get('holdTime', 0) if q_info else 0
        max_callers = q_info.get('maxCallers', 0) if q_info else 0
        
        interrupt_mode = q_info.get('holdAudioInterruptionMode', 'Never') if q_info else 'Never'
        interrupt_period = q_info.get('holdAudioInterruptionPeriod', 0) if q_info else 0
        
        hold_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map) if q_info else "Voicemail"
        max_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map) if q_info else "Voicemail"

        # --- Caller Experience ---
        if greetings.get('intro'):
            add_case("Caller Experience", "Queue Intro Greeting", 
                     "Place a call to the queue.", 
                     "The custom Introductory Greeting plays completely before the call begins ringing agents or playing hold music.")
        
        add_case("Caller Experience", "Call Recording Prompt", 
                 "Place a call to the queue.", 
                 "If Automatic Call Recording is enabled, the 'This call is being recorded' prompt plays before the agent connects.")
        
        add_case("Caller Experience", "Connecting Audio / Hold Music", 
                 "Remain in the queue while waiting for an available agent.", 
                 "The configured Hold Music or Promotional Greeting plays cleanly.")
        
        if interrupt_mode != 'Never' and interrupt_period > 0:
            add_case("Caller Experience", f"Interrupt Audio ({interrupt_period}s)", 
                     f"Remain on hold in the queue for at least {interrupt_period + 5} seconds.", 
                     f"At exactly {interrupt_period} seconds, the hold music pauses, the interrupt audio prompt plays, and hold music resumes.")

        # --- Agent Tests ---
        add_case("Agent Tests", "Queue Opt-In", 
                 f"Agent logs into the RingCentral App and toggles 'Accept Queue Calls' to ON. Place a test call.", 
                 f"Agent's device rings. The Queue Name '{extension_name}' is clearly displayed on the Caller ID.")
        
        add_case("Agent Tests", "Queue Opt-Out / DND", 
                 "Agent toggles 'Accept Queue Calls' to OFF. Place a test call.", 
                 "Agent's device does NOT ring. The call smoothly hunts to the next available agent.")

        add_case("Agent Tests", "Active Call Decline", 
                 "While the queue call is ringing an agent, the agent clicks 'Decline'.", 
                 "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller.")
        
        add_case("Agent Tests", "Agent Busy", 
                 "Have the agent place an outbound call to become busy. Place a new call into the queue.", 
                 "The system registers the agent as busy. The queue call hunts to the next available agent instead of interrupting.")

        if wrap_up_time > 0:
            add_case("Agent Tests", f"Wrap-Up / ACW Timer ({wrap_up_time}s)", 
                     f"Agent answers a queue call and hangs up. Immediately place a second call into the queue.", 
                     f"Agent enters 'Wrap-Up' status and does NOT ring again until the {wrap_up_time} second timer expires.")

        # --- Distribution Logic ---
        if transfer_mode == 'Simultaneous':
            add_case("Agent Tests", "Distribution: Simultaneous", 
                     "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                     "The call rings ALL available queue members at the exact same time.")
        else:
            add_case("Agent Tests", f"Distribution: {transfer_mode}", 
                     "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                     f"The call cascades to agents sequentially based on the {transfer_mode} logic.")
            
            add_case("Agent Tests", f"Agent Ring Timeout ({agent_timeout}s)", 
                     f"The targeted agent lets the call ring without answering or declining for exactly {agent_timeout} seconds.", 
                     f"The {agent_timeout}s timer expires. The call drops from Agent 1 and begins ringing Agent 2.")

        # --- Agent Call Handling ---
        add_case("Agent Call Handling", "Call Hold", 
                 "Agent answers the queue call and places the caller on hold using the RingCentral App.", 
                 "The caller hears the agent hold music. The call can be successfully retrieved by the agent.")
        
        add_case("Agent Call Handling", "Warm Transfer", 
                 "Agent answers the queue call, initiates a Warm Transfer to an internal extension, consults, and completes the transfer.", 
                 "The caller is successfully connected to the secondary extension with two-way audio.")
        
        add_case("Agent Call Handling", "Blind Transfer", 
                 "Agent answers the queue call and initiates a Blind Transfer to an internal extension.", 
                 "The agent is immediately released. The caller is transferred and hears ringing to the secondary extension.")
        
        add_case("Agent Call Handling", "Call Park", 
                 "Agent answers the queue call and Parks the call to a Park Location (e.g., *801).", 
                 "The caller is parked and hears hold music. The call can be successfully retrieved by another user dialing the park extension.")

        # --- Overflow & Boundaries ---
        add_case("Overflow & Boundaries", "Zero Agents Logged In", 
                 "Ensure ALL assigned agents are Logged Out or on DND. Initiate a call.", 
                 "Call bypasses queue hold music and triggers 'No Members Available' overflow routing.")

        if hold_time > 0:
            add_case("Overflow & Boundaries", f"Max Wait Time Limit ({hold_time}s)", 
                     f"Remain on hold in the queue until the {hold_time} second limit is reached.", 
                     f"The {hold_time}s timer expires. Call is removed from the queue and executes overflow to: [{hold_dest}].")
        
        if max_callers > 0:
            add_case("Overflow & Boundaries", f"Max Callers Limit ({max_callers})", 
                     f"Simultaneously flood the queue with {max_callers + 1} concurrent inbound calls.", 
                     f"The final call breaches the capacity limit of {max_callers}. It bypasses hold music and executes overflow to: [{max_dest}].")

        add_case("Overflow & Boundaries", "Zero-Out Exception", 
                 "While listening to the queue hold music, press '0' on the dialpad.", 
                 "If a zero-out operator is configured, the call escapes the queue. If not configured, the DTMF input is gracefully ignored.")

    # ==========================================
    # CATEGORY: IVR MENU TESTS
    # ==========================================
    elif extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info:
            prompt_data = ivr_info.get('prompt', {})
            prompt_desc = f"Text-to-Speech prompt: '{prompt_data.get('text', '...')}'" if 'text' in prompt_data else "Uploaded Audio prompt"
            
            add_case("IVR Tests", "Audio Quality & Script", 
                     "Dial the IVR menu.", 
                     f"{prompt_desc} plays cleanly. Wording matches the approved script.")
            add_case("IVR Tests", "Barge-in (Interruptibility)", 
                     "While the greeting is actively playing, press a valid menu key.", 
                     "IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the full greeting.")
            
            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    
                    act_type = act.get('action', 'Unknown')
                    if act_type == 'Transfer':
                        target = resolve_target(act, ext_map)
                        expected_str = f"Transfers the call to: [{target}]. Verify target rings."
                    elif act_type == 'TakeMessagesReturnToGreeting':
                        target = resolve_target(act, ext_map)
                        expected_str = f"Transfers the call directly to Voicemail of: [{target}]."
                    elif act_type == 'Forward':
                        expected_str = f"Forwards to external number: {act.get('phoneNumber', 'Unknown')}."
                    else:
                        expected_str = f"Triggers {act_type} logic."
                    
                    add_case("IVR Navigation", f"Key Mapping: Press '{key}'", 
                             f"Listen to prompt and press '{key}' on the dialpad.", 
                             expected_str)
            
            add_case("Overflow & Boundaries", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "System plays an 'Invalid entry' error prompt and replays the main menu.")
            add_case("Overflow & Boundaries", "Timeout (No Input)", 
                     "Listen to the entire IVR prompt and provide no DTMF input.", 
                     "System times out, replays the menu, and eventually executes the default timeout routing.")

    # ==========================================
    # CATEGORY: VOICEMAIL & ADMIN
    # ==========================================
    add_case("Voicemail", "Voicemail Deposit", 
             "Trigger a routing scenario (e.g., Out of Hours or Queue Overflow) that routes to Voicemail. Leave a 10-second test message.", 
             "The correct Voicemail greeting plays. The message is successfully recorded without truncating.")
    add_case("Voicemail", "Voicemail Delivery", 
             "Check the designated target's inbox (Email Notification or RingCentral App).", 
             "The voicemail audio file (.mp3) is delivered accurately. Voice-to-text transcript is included if enabled.")

    add_case("Administration Tasks", "Call Logs Generation", 
             "Log into the RingCentral Admin Portal and navigate to Analytics > Call Logs.", 
             "The test calls are accurately reflected, showing the correct originating Caller ID, target extension, duration, and final result.")
    
    if extension_type == 'Department':
        add_case("Administration Tasks", "Queue Reporting Visibility", 
                 "Navigate to Analytics > Live Reports / Queue Reports.", 
                 "The queue test calls correctly update the Service Level, Wait Time, and Abandoned metrics.")

    add_case("Administration Tasks", "Call Recording Retrieval", 
             "If Automatic Call Recording is enabled, navigate to the Call Recordings section in the Admin Portal.", 
             "The recording of the test call is present, playable, and clearly captures both legs of the audio.")

    return uat_cases
