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

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Generates an exhaustive, enterprise-grade UAT script tailored to the entity type."""
    uat_cases = []
    case_counter = 1

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

    # --- 1. BASE CONNECTIVITY (ALL TYPES) ---
    add_case("1. Connectivity", "Internal Dialing", 
             f"Dial {extension_number} from an internal RingEX app or deskphone.", 
             f"Call connects successfully to {extension_name} without dead air or SIP errors.")
    add_case("1. Connectivity", "External Dialing", 
             f"Dial the external Direct Inward Dialing (DID) number assigned to {extension_name} from a mobile phone.", 
             f"Call connects via the PSTN with high-quality, two-way audio.")

    # --- 2. EXHAUSTIVE CALL QUEUE (DEPARTMENT) TESTS ---
    if extension_type == 'Department':
        # Agent Experience
        add_case("2. Agent Experience", "Agent Login / Accept Calls", 
                 "Have a queue agent toggle 'Accept Queue Calls' to ON in the RingEX App. Place a test call.", 
                 "The agent's device rings with the incoming queue call, displaying the Queue Name on the caller ID.")
        add_case("2. Agent Experience", "Agent Logout / DND", 
                 "Have the agent toggle 'Accept Queue Calls' to OFF. Place a test call.", 
                 "The agent's device does NOT ring. The call correctly hunts to the next available agent.")
        add_case("2. Agent Experience", "Active Call Decline", 
                 "Agent actively presses 'Decline' on the incoming queue call.", 
                 "The call immediately stops ringing that agent and routes to the next available agent without dropping the caller.")
        add_case("2. Agent Experience", "Missed Call (Ring Timeout)", 
                 "Agent lets the call ring without answering or declining.", 
                 "The call rings for the configured duration (e.g., 4 rings), then automatically moves to the next available agent.")
        add_case("2. Agent Experience", "After Call Work (ACW)", 
                 "Agent answers a queue call and hangs up. Place another call immediately into the queue.", 
                 "Agent enters Wrap-Up/ACW status and does not receive the new call until the configured ACW timer expires.")
        
        # Queue Routing & Overflows
        add_case("3. Queue Boundaries", "Queue Distribution Check", 
                 "Ensure 3 agents are available. Place 3 concurrent calls into the queue.", 
                 "Calls are distributed to the agents according to the configured routing method (e.g., Rotating, Simultaneous).")
        add_case("3. Queue Boundaries", "Hold Music Verification", 
                 "Call the queue and remain on hold.", 
                 "The officially approved Hold Music or custom promotional messaging plays cleanly without distortion.")
        add_case("3. Queue Boundaries", "Max Wait Time Overflow", 
                 "Call the queue and remain on hold until the Maximum Wait Time expires.", 
                 "The call is automatically removed from the queue and routed to the configured Primary Overflow destination (e.g., Voicemail).")
        add_case("3. Queue Boundaries", "Max Callers Capacity Overflow", 
                 "Simultaneously flood the queue with concurrent test calls up to the maximum queue capacity.", 
                 "The final call that breaches the capacity limit instantly triggers the 'Max Callers' overflow action without playing hold music.")
        add_case("3. Queue Boundaries", "Zero Agents Available Overflow", 
                 "Ensure ALL assigned agents are logged out. Initiate a call to the queue.", 
                 "The call immediately bypasses the queue and follows 'No Members Available' routing rules.")
        add_case("3. Queue Boundaries", "Queue Zero-Out Exception", 
                 "While listening to queue hold music, press '0' on the dialpad.", 
                 "If configured, the call escapes the queue and routes to the designated operator. Otherwise, the DTMF input is ignored gracefully.")

        # Voicemail
        add_case("4. Voicemail", "Voicemail Deposit", 
                 "Route a call to the queue's voicemail and leave a 10-second test message.", 
                 "The correct queue voicemail greeting plays. The message is successfully recorded.")
        add_case("4. Voicemail", "Voicemail Delivery", 
                 "Check the designated Voicemail recipient's inbox (Email or RingEX App).", 
                 "The voicemail audio file and transcript (if enabled) are delivered to the correct inbox.")

    # --- 3. EXHAUSTIVE IVR MENU TESTS ---
    elif extension_type == 'IvrMenu':
        add_case("2. IVR Prompts", "Audio Quality & Script", 
                 "Dial the IVR menu.", 
                 "The IVR audio prompt plays cleanly. The wording matches the officially approved script exactly.")
        add_case("2. IVR Prompts", "Barge-in (Interrupt)", 
                 "Dial the IVR menu. While the greeting is still playing, press a valid menu key.", 
                 "The IVR accepts the input immediately without forcing the caller to listen to the entire message.")
        
        add_case("3. IVR Navigation", "Valid Key Press Routing", 
                 "Listen to the IVR prompt and press a valid, configured menu key (e.g., '1' or '2').", 
                 "The system registers the DTMF tone and transfers the call to the correct destination.")
        add_case("3. IVR Navigation", "Multi-Digit Extension Dialing", 
                 "While in the IVR, dial a known 3 or 4-digit internal extension number.", 
                 "If enabled, the IVR intercepts the dial string and transfers the caller directly to that internal extension.")
        
        add_case("4. IVR Boundaries", "Invalid Key Press", 
                 "Press an unassigned key on the dialpad (e.g., '9' or '#').", 
                 "The system plays an 'Invalid entry' prompt and replays the main menu.")
        add_case("4. IVR Boundaries", "Timeout (No Input)", 
                 "Listen to the entire IVR prompt and provide no DTMF input.", 
                 "The system times out. It either replays the menu (typically up to 3 times) or executes the default timeout routing (e.g., transfer to operator).")

    # --- 4. TIME OF DAY (ALL TYPES) ---
    add_case("5. Time of Day", "Business Hours", 
             "Initiate a call during configured Open Hours.", 
             "Call follows standard Business Hours routing.")
    add_case("5. Time of Day", "After Hours", 
             "Initiate a call outside of configured Business Hours.", 
             "Call follows After Hours routing (e.g., plays closed greeting and routes to Voicemail).")
    add_case("5. Time of Day", "Holiday Routing", 
             "Initiate a call during a pre-configured Holiday schedule.", 
             "Call follows Holiday routing and plays the specific Holiday announcement.")

    # --- 5. TERMINATION ---
    add_case("6. Termination", "Clean Disconnect", 
             "During an active connected state, the caller hangs up.", 
             "The call drops immediately. RingCentral generates accurate call logs and agents return to 'Available' status.")

    return uat_cases
