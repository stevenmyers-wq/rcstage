# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, Sites, and Shared Lines for testing."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    
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
    """Builds a global directory to translate raw IDs into readable names."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    ext_map = {}
    if response and 'records' in response:
        for ext in response['records']:
            ext_map[str(ext['id'])] = f"{ext.get('name', 'Unknown')} (Ext {ext.get('extensionNumber', 'N/A')})"
    return ext_map

def get_direct_numbers(ext_id):
    """Forensically extracts ONLY explicitly assigned Direct Inward Dialing (DID) numbers."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    numbers = []
    if response and 'records' in response:
        for record in response['records']:
            # Strict filter to prevent pulling shared company lines or hidden routing numbers
            if record.get('usageType') == 'DirectNumber':
                numbers.append(record.get('phoneNumber'))
    return ", ".join(numbers) if numbers else None

def get_business_hours_string(ext_id):
    """Extracts explicit schedules, falling back to account level if set to 24/7."""
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
                time_str = f"{times[0].get('from', '')}-{times[0].get('to', '')}"
                days.append(f"{day[:3]} {time_str}")
        return ", ".join(days) if days else "Custom Schedule"
    return "Custom/Specific Schedule"

def resolve_target(action_obj, ext_map):
    """Resolves nested JSON overflow targets into human-readable destinations."""
    if not action_obj: return "Unknown Destination"
    
    target_id = str(action_obj.get('extension', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return ext_map[target_id]
        
    target_id = str(action_obj.get('recipient', {}).get('id', ''))
    if target_id and target_id in ext_map:
        return ext_map[target_id]
        
    num = action_obj.get('phoneNumber')
    if num: return f"External Number: {num}"
    
    return "Unknown Target"

def get_queue_greetings(ext_id):
    """Fetches explicit greeting configurations for the queue."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting', method='GET', raise_error=False)
    greetings = {'intro': False, 'audio_while_connecting': False, 'voicemail': False}
    if response and 'records' in response:
        for g in response['records']:
            if g.get('type') == 'Introductory' and g.get('preset') != 'Default':
                greetings['intro'] = True
            if g.get('type') == 'ConnectingAudio':
                greetings['audio_while_connecting'] = True
            if g.get('type') == 'Voicemail':
                greetings['voicemail'] = True
    return greetings

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Generates an exhaustive UAT matrix matching corporate standards."""
    uat_cases = []
    case_counter = 1
    
    ext_map = build_extension_map()
    direct_numbers = get_direct_numbers(extension_id)
    bh_string = get_business_hours_string(extension_id)

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
    # CATEGORY: INTEGRATION
    # ==========================================
    add_case("Integration", "Internal Routing", 
             f"Dial extension {extension_number} from an internal device.", 
             f"Call connects successfully to {extension_name} without SIP 4xx/5xx errors.")
    
    if direct_numbers:
        add_case("Integration", "External Routing (DID)", 
                 f"Dial the external Direct Inward Dialing (DID) number assigned to {extension_name} ({direct_numbers}) from a mobile phone.", 
                 "Call connects via the PSTN with high-quality, two-way audio.")
    else:
        add_case("Integration", "External Routing (No DID)", 
                 f"Dial the Main Company Number. When prompted, enter extension {extension_number}.", 
                 f"Call successfully routes from the auto-receptionist to {extension_name}.")

    add_case("Integration", "Business Hours Validation", 
             f"Initiate a test call during the configured Open Hours: [{bh_string}].", 
             "Call follows the primary 'Open' routing path.")

    # ==========================================
    # CATEGORY: CALL QUEUE TESTS
    # ==========================================
    if extension_type == 'Department':
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False)
        greetings = get_queue_greetings(extension_id)
        
        if q_info:
            transfer_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15)
            wrap_up_time = q_info.get('wrapUpTime', 0)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            
            interrupt_mode = q_info.get('holdAudioInterruptionMode', 'Never')
            interrupt_period = q_info.get('holdAudioInterruptionPeriod', 0)
            
            hold_action = q_info.get('holdTimeExpirationAction', 'Unknown')
            max_call_action = q_info.get('maxCallersAction', 'Unknown')
            
            hold_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)
            max_dest = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), ext_map)

            # --- Queue Experience ---
            if greetings['intro']:
                add_case("Call Queue Tests", "Introductory Greeting", 
                         "Place a call to the queue.", 
                         "The custom Intro Greeting plays completely before the call begins ringing agents or playing hold music.")
            
            add_case("Call Queue Tests", "Hold Music Verification", 
                     "Remain in the queue while agents are busy/ringing.", 
                     "The configured connecting audio/hold music plays cleanly without jitter.")
            
            if interrupt_mode != 'Never' and interrupt_period > 0:
                add_case("Call Queue Tests", f"Interrupt Audio ({interrupt_period}s)", 
                         f"Remain on hold in the queue for at least {interrupt_period + 5} seconds.", 
                         f"At exactly {interrupt_period} seconds, the hold music pauses, the interrupt audio prompt plays, and hold music resumes.")

            # --- Agent Operations ---
            add_case("Agent Tests", "Queue Opt-In", 
                     f"Agent logs into the RC App and toggles 'Accept Queue Calls' to ON for {extension_name}. Place a test call.", 
                     f"Agent's device rings. The Queue Name is prepended to the Caller ID display.")
            
            add_case("Agent Tests", "Active Call Decline", 
                     "While the queue call is ringing an agent, the agent clicks 'Decline'.", 
                     "Ringing stops for that agent immediately. Call hunts to the next available agent without dropping the caller.")

            if wrap_up_time > 0:
                add_case("Agent Tests", f"Wrap-Up / ACW Timer ({wrap_up_time}s)", 
                         f"Agent answers a queue call and hangs up. Immediately place a second call into the queue.", 
                         f"Agent enters 'Wrap-Up' status and does NOT ring again until the {wrap_up_time} second ACW timer expires.")

            # --- Distribution Logic ---
            if transfer_mode == 'Simultaneous':
                add_case("Call Queue Tests", "Distribution: Simultaneous", 
                         "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                         "The call rings ALL available queue members at the exact same time.")
            else:
                add_case("Call Queue Tests", f"Distribution: {transfer_mode}", 
                         "Ensure multiple queue agents are 'Available'. Place a call into the queue.", 
                         f"The call cascades to agents based on {transfer_mode} logic.")
                
                # Explicity ONLY test agent ring timeout if the queue is NOT simultaneous.
                add_case("Agent Tests", f"Ring Timeout ({agent_timeout}s)", 
                         f"The targeted agent lets the call ring without answering or declining for exactly {agent_timeout} seconds.", 
                         f"The {agent_timeout}s timer expires. The call drops from Agent 1 and begins ringing Agent 2.")

            # --- Overflows & Boundaries ---
            add_case("Overflow Tests", "Zero Agents Logged In", 
                     "Ensure ALL assigned agents are Logged Out or on DND. Initiate a call.", 
                     "Call bypasses queue hold music and triggers 'No Members Available' overflow routing.")

            if hold_time > 0:
                add_case("Overflow Tests", f"Max Wait Time Limit ({hold_time}s)", 
                         f"Remain on hold in the queue until the {hold_time} second limit is reached.", 
                         f"The {hold_time}s timer expires. Call is removed from the queue and executes: [{hold_action}] -> {hold_dest}.")
            
            if max_callers > 0:
                add_case("Overflow Tests", f"Max Callers Limit ({max_callers})", 
                         f"Simultaneously flood the queue with {max_callers + 1} concurrent inbound calls.", 
                         f"The final call breaches the capacity limit of {max_callers}. It bypasses hold music and executes: [{max_call_action}] -> {max_dest}.")

            if 'Voicemail' in str(hold_dest) or 'Voicemail' in str(max_dest) or hold_action == 'TakeMessagesReturnToGreeting':
                add_case("Voicemail Tests", "Overflow Voicemail Deposit", 
                         "Trigger a queue overflow that routes to Voicemail. Leave a 10-second test message.", 
                         "The correct Voicemail greeting plays. The message is successfully recorded.")
                add_case("Voicemail Tests", "Voicemail Delivery", 
                         "Check the designated target's inbox.", 
                         "The voicemail audio file is delivered accurately.")

    # ==========================================
    # CATEGORY: IVR MENU TESTS
    # ==========================================
    elif extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info:
            prompt_data = ivr_info.get('prompt', {})
            prompt_desc = f"Text-to-Speech prompt: '{prompt_data.get('text', '...')}'" if 'text' in prompt_data else "Uploaded Audio prompt"
            
            add_case("IVR Menu Tests", "Audio Quality & Script", 
                     "Dial the IVR menu.", 
                     f"{prompt_desc} plays cleanly. Wording matches the approved script.")
            add_case("IVR Menu Tests", "Barge-in (Interruptibility)", 
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
                    
                    add_case("IVR Menu Tests", f"Key Mapping: Press '{key}'", 
                             f"Listen to prompt and press '{key}' on the dialpad.", 
                             expected_str)
            
            add_case("IVR Menu Tests", "Invalid Key Press", 
                     "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                     "System plays an 'Invalid entry' error prompt and replays the main menu.")
            add_case("IVR Menu Tests", "Timeout (No Input)", 
                     "Listen to the entire IVR prompt and provide no DTMF input.", 
                     "System times out, replays the menu, and eventually executes the default timeout routing.")

    add_case("Administration Tasks", "Clean Disconnect", 
             "During any active connected state, have the caller hang up.", 
             "Call drops immediately. RingCentral generates accurate Call Log data.")

    return uat_cases
