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
    numbers = [r.get('phoneNumber') for r in response.get('records', []) if r.get('usageType') == 'DirectNumber']
    return ", ".join(numbers) if numbers else "Auto-Receptionist"

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

def resolve_target(action_obj, ext_map):
    if not action_obj: return "Configured Destination"
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map: return ext_map[target_id]
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map: return f"Voicemail of {ext_map[target_id]}"
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}"
    return "Configured Target"

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
    # 1. INTEGRATION & CONNECTIVITY 
    # ==========================================
    add_case("Integration", "Internal Routing", 
             f"Dial extension {extension_number} from an internal RingCentral app or deskphone.", 
             f"Call connects successfully to {extension_name} without dead air or SIP errors.")
    
    add_case("Integration", "External Routing", 
             f"Dial {extension_name} via external channels (DID: {did_numbers}).", 
             "Call connects via the PSTN with high-quality, two-way audio.")

    add_case("Integration", "Audio Quality Check", 
             "Maintain an active connected call with the target for at least 2 minutes. Speak continuously.", 
             "Audio remains high-fidelity, two-way, with no noticeable jitter, latency, clipping, or packet loss.")
             
    add_case("Integration", "Business Hours Validation", 
             f"Initiate a test call during Open Hours: [{bh_string}].", 
             "Call follows the primary 'Open' routing path.")
             
    add_case("Integration", "Out-of-Hours Validation", 
             f"Initiate a test call OUTSIDE of Open Hours: [{bh_string}].", 
             "Call intercepts and follows 'Closed' routing (e.g., plays After Hours greeting or routes to Voicemail).")
                 
    add_case("Integration", "Holiday Routing Validation", 
             "Temporarily configure a Holiday rule for today's date in the portal. Initiate a test call.", 
             "Call follows the configured Holiday routing, overriding both standard business and after-hours rules.")

    # ==========================================
    # 2. CALL QUEUE EXHAUSTIVE TESTS (Always Generates)
    # ==========================================
    if extension_type == 'Department':
        # Safely fetch API data, fallback to placeholder strings if API fails
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False) or {}
        
        transfer_mode = q_info.get('transferMode', '[Configured Distribution Method]')
        agent_timeout = q_info.get('agentTimeout', '[Configured Timeout]')
        wrap_up_time = q_info.get('wrapUpTime', '[Configured Wrap-Up]')
        hold_time = q_info.get('holdTime', '[Configured Max Wait]')
        max_callers = q_info.get('maxCallers', '[Configured Limit]')
        interrupt_period = q_info.get('holdAudioInterruptionPeriod', '[Configured Interval]')
        
        hold_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail'), ext_map)
        max_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail'), ext_map)

        # --- CALLER EXPERIENCE ---
        add_case("Queue - Caller Experience", "Introductory Greeting", 
                 "Place a call to the queue.", 
                 "If configured, the Introductory Greeting plays completely before the call begins ringing agents or playing hold music.")
        
        add_case("Queue - Caller Experience", "Call Recording Prompt", 
                 "Place a call to the queue.", 
                 "If Automatic Call Recording is enabled, the 'This call is being recorded' prompt plays before the agent connects.")
        
        add_case("Queue - Caller Experience", "Connecting Audio / Hold Music", 
                 "Remain in the queue while waiting for an available agent.", 
                 "The configured Hold Music or Promotional Greeting plays cleanly without distortion.")
        
        add_case("Queue - Caller Experience", "Interrupt Audio Verification", 
                 f"Remain on hold in the queue for at least {interrupt_period} seconds.", 
                 "If configured, the hold music pauses, the interrupt audio prompt (e.g., 'Please continue to hold') plays, and music resumes.")

        # --- AGENT BEHAVIOR & STATES ---
        add_case("Queue - Agent Tests", "Queue Opt-In", 
                 f"Agent logs into the RC App and toggles 'Accept Queue Calls' to ON for {extension_name}. Place a test call.", 
                 f"Agent's device rings. The Queue Name '{extension_name}' is clearly prepended to the Caller ID display.")
        
        add_case("Queue - Agent Tests", "Queue Opt-Out / DND", 
                 "Agent toggles 'Accept Queue Calls' to OFF. Place a test call.", 
                 "Agent's device does NOT ring. The call smoothly hunts to the next available agent.")

        add_case("Queue - Agent Tests", "Active Call Decline", 
                 "While the queue call is ringing an agent, the agent clicks 'Decline'.", 
                 "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller.")
        
        add_case("Queue - Agent Tests", "Agent Busy (External Call)", 
                 "Have the agent place an outbound call to become busy. Place a new call into the queue.", 
                 "The system registers the agent as busy. The queue call hunts to the next available agent instead of interrupting the active call.")

        add_case("Queue - Agent Tests", f"Wrap-Up / ACW Timer", 
                 f"Agent answers a queue call and hangs up. Immediately place a second call into the queue.", 
                 f"Agent enters 'Wrap-Up' status and does NOT ring again until the {wrap_up_time}s ACW timer expires.")

        # --- DISTRIBUTION LOGIC ---
        add_case("Queue - Distribution", f"Distribution Method: {transfer_mode}", 
                 "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                 f"The call distributes to agents sequentially or simultaneously based on the {transfer_mode} logic.")
        
        add_case("Queue - Distribution", "Agent Ring Timeout", 
                 f"The targeted agent lets the call ring without answering or declining for {agent_timeout} seconds.", 
                 "The timer expires. The call drops from Agent 1 and begins ringing Agent 2.")

        # --- CALL HANDLING (TRANSFERS & PARKS) ---
        add_case("Queue - Call Handling", "Call Hold", 
                 "Agent answers the queue call and places the caller on hold using the RingCentral App.", 
                 "The caller hears the agent hold music. The call can be successfully retrieved by the agent.")
        
        add_case("Queue - Call Handling", "Warm Transfer", 
                 "Agent answers the queue call, initiates a Warm Transfer to an internal extension, consults, and completes the transfer.", 
                 "The caller is successfully connected to the secondary extension with two-way audio.")
        
        add_case("Queue - Call Handling", "Blind Transfer", 
                 "Agent answers the queue call and initiates a Blind Transfer to an internal extension.", 
                 "The agent is immediately released. The caller is transferred and hears ringing to the secondary extension.")
        
        add_case("Queue - Call Handling", "Call Park", 
                 "Agent answers the queue call and Parks the call to a Park Location (e.g., *801).", 
                 "The caller is parked and hears hold music. The call can be successfully retrieved by another user dialing the park extension.")

        # --- OVERFLOWS & BOUNDARIES ---
        add_case("Queue - Overflows", "Zero Agents Logged In", 
                 "Ensure ALL assigned agents are Logged Out or on DND. Initiate a call.", 
                 "Call bypasses queue hold music and triggers 'No Members Available' overflow routing.")

        add_case("Queue - Overflows", f"Max Wait Time Limit", 
                 f"Remain on hold in the queue until the {hold_time}s wait limit is reached.", 
                 f"Timer expires. Call is removed from the queue and executes overflow to: [{hold_dest}].")
        
        add_case("Queue - Overflows", f"Max Callers Limit", 
                 f"Simultaneously flood the queue with {max_callers} concurrent inbound calls, plus one extra.", 
                 f"The final call breaches the capacity limit. It bypasses hold music and executes overflow to: [{max_dest}].")

        add_case("Queue - Overflows", "Zero-Out Exception", 
                 "While listening to the queue hold music, press '0' on the dialpad.", 
                 "If a zero-out operator is configured, the call escapes the queue. If not configured, the DTMF input is gracefully ignored.")

    # ==========================================
    # 3. IVR MENU EXHAUSTIVE TESTS 
    # ==========================================
    elif extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False) or {}
        
        add_case("IVR Tests", "Audio Quality & Script", 
                 "Dial the IVR menu.", 
                 "The IVR prompt plays cleanly. Wording exactly matches the approved script.")
                 
        add_case("IVR Tests", "Barge-in (Interruptibility)", 
                 "While the greeting is actively playing, press a valid menu key.", 
                 "IVR registers the DTMF tone immediately and routes the call without forcing the caller to listen to the full greeting.")
        
        if 'actions' in ivr_info:
            for act in ivr_info['actions']:
                key = act.get('input', '')
                if not key: continue 
                
                target = resolve_target(act, ext_map)
                add_case("IVR Navigation", f"Key Mapping: Press '{key}'", 
                         f"Listen to prompt and press '{key}' on the dialpad.", 
                         f"Executes configured routing. Verifies transfer to: [{target}].")
        else:
            # Fallback if API blocks action read
            for i in range(1, 4):
                 add_case("IVR Navigation", f"Key Mapping: Press '{i}'", 
                         f"Listen to prompt and press '{i}' on the dialpad.", 
                         "Executes configured routing for that specific key.")
        
        add_case("IVR Navigation", "Dial-By-Extension Verification", 
                 "While in the IVR, enter a known internal user's 3 or 4-digit extension number.", 
                 "If general extension dialing is permitted, the IVR intercepts the string and transfers the call to the user.")

        add_case("IVR Boundaries", "Invalid Key Press", 
                 "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                 "System plays an 'Invalid entry' error prompt and replays the main menu.")
                 
        add_case("IVR Boundaries", "Timeout (No Input)", 
                 "Listen to the entire IVR prompt and provide no DTMF input.", 
                 "System times out, replays the menu, and eventually executes the default timeout routing.")

    # ==========================================
    # 4. VOICEMAIL & ADMIN TASKS (All Types)
    # ==========================================
    add_case("Voicemail Tests", "Voicemail Deposit", 
             "Trigger a routing scenario (e.g., Out of Hours or Queue Overflow) that routes to Voicemail. Leave a 10-second test message.", 
             "The correct Voicemail greeting plays. The message is successfully recorded without truncating.")
             
    add_case("Voicemail Tests", "Voicemail Delivery", 
             "Check the designated target's inbox (Email Notification or RingCentral App).", 
             "The voicemail audio file (.mp3) is delivered accurately. Voice-to-text transcript is included if enabled on the account.")

    add_case("Administration Tasks", "Call Logs Generation", 
             "Log into the RingCentral Admin Portal and navigate to Analytics > Call Logs.", 
             "The test calls are accurately reflected, showing the correct originating Caller ID, target extension, duration, and final result.")
    
    if extension_type == 'Department':
        add_case("Administration Tasks", "Queue Reporting Visibility", 
                 "Navigate to Analytics > Live Reports / Queue Reports.", 
                 "The queue test calls correctly update the Service Level, Wait Time, and Abandoned metrics in near real-time.")

    add_case("Administration Tasks", "Call Recording Retrieval", 
             "If Automatic Call Recording is enabled, navigate to the Call Recordings section in the Admin Portal.", 
             "The recording of the test call is present, playable, and clearly captures both legs of the audio.")

    return uat_cases
