# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

# =============================================================================
# DATA EXTRACTION HELPERS
# =============================================================================

def get_testable_extensions():
    """Fetches base call flows for the UI dropdown. Excludes standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [
        {"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']}
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    return sorted(entities, key=lambda x: x['name'])

def build_global_directory():
    """Builds a cached dictionary of all extensions to resolve IDs into actual Names/Numbers."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    directory = {}
    if response and 'records' in response:
        for ext in response['records']:
            directory[str(ext['id'])] = {
                "name": ext.get('name', 'Unknown'),
                "ext": ext.get('extensionNumber', 'N/A'),
                "type": ext.get('type', 'Unknown')
            }
    return directory

def extract_strict_dids(ext_id):
    """Pulls ONLY the Direct Inward Dialing numbers explicitly bound to this specific extension ID."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    dids = []
    if response and 'records' in response:
        for r in response['records']:
            if r.get('usageType') == 'DirectNumber' and str(r.get('extension', {}).get('id', '')) == str(ext_id):
                dids.append(r.get('phoneNumber'))
    return dids

def extract_business_hours(ext_id):
    """Pulls explicit business hours, safely falling back to the account level if inherited."""
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

def extract_greetings(ext_id):
    """Queries the exact greeting toggles for the extension."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/greeting', method='GET', raise_error=False)
    greetings = {'intro': False, 'hold_music': False, 'voicemail': False}
    if response and 'records' in response:
        for g in response['records']:
            if g.get('type') == 'Introductory' and g.get('preset') != 'Default': greetings['intro'] = True
            if g.get('type') == 'ConnectingAudio': greetings['hold_music'] = True
            if g.get('type') == 'Voicemail': greetings['voicemail'] = True
    return greetings

def resolve_target(action_obj, directory):
    """Maps a raw API target object to a human-readable destination and returns its ID for recursive crawling."""
    if not action_obj: return "Disconnect / System Default", None
    
    if 'extension' in action_obj:
        t_id = str(action_obj['extension'].get('id', ''))
        if t_id in directory: return f"{directory[t_id]['name']} (Ext {directory[t_id]['ext']})", t_id
        
    if 'recipient' in action_obj:
        t_id = str(action_obj['recipient'].get('id', ''))
        if t_id in directory: return f"Voicemail of {directory[t_id]['name']}", t_id
        
    if 'phoneNumber' in action_obj:
        return f"External Number: {action_obj['phoneNumber']}", None
        
    return "Configured Destination", None

def parse_custom_rule_triggers(rule):
    """Reads the exact caller ID, called number, or schedule triggers for a custom rule."""
    triggers = []
    if rule.get('callers'):
        c_ids = [c.get('callerId', '') for c in rule['callers']]
        triggers.append(f"Caller ID is {', '.join(c_ids)}")
    if rule.get('calledNumbers'):
        nums = [n.get('phoneNumber', '') for n in rule['calledNumbers']]
        triggers.append(f"Dialed Number is {', '.join(nums)}")
    if rule.get('schedule'):
        triggers.append("Time/Date matches custom schedule")
    return " AND ".join(triggers) if triggers else "Unknown Trigger"


# =============================================================================
# DATA-DRIVEN UAT GENERATOR
# =============================================================================

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    uat_cases = []
    case_counter = 1
    directory = build_global_directory()
    
    # Queue for recursive crawling (Mapping the whole journey)
    nodes_to_process = [{
        "id": str(extension_id),
        "name": extension_name,
        "ext": extension_number,
        "type": extension_type,
        "context": "Primary Call Flow"
    }]
    processed_nodes = set()

    def add_row(category, scenario, step, expected):
        nonlocal case_counter
        uat_cases.append({
            "test_id": f"UAT-{case_counter:04d}",
            "category": category,
            "scenario": scenario,
            "action": step,
            "expected": expected
        })
        case_counter += 1

    # CRAWL THE TREE
    while nodes_to_process:
        current = nodes_to_process.pop(0)
        c_id = current['id']
        c_name = current['name']
        c_ext = current['ext']
        c_type = current['type']
        ctx = current['context']
        
        if c_id in processed_nodes: continue
        processed_nodes.add(c_id)

        cat_prefix = f"[{c_name}] "
        
        # 1. PULL ALL API DATA FOR THIS NODE
        dids = extract_strict_dids(c_id)
        bh_str = extract_business_hours(c_id)
        greetings = extract_greetings(c_id)
        answering_rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/answering-rule', method='GET', raise_error=False) or {'records': []}

        # 2. CONNECTIVITY ROWS
        if ctx == "Primary Call Flow":
            if dids:
                for did in dids:
                    add_row(f"{cat_prefix}Integration", f"External Routing (DID: {did})", f"Dial {did} from an external mobile.", f"Call connects to {c_name} via PSTN with 2-way audio.")
            else:
                add_row(f"{cat_prefix}Integration", "Internal Routing", f"Dial {c_ext} from an internal RingCentral device.", f"Call connects to {c_name}.")

        # 3. ROUTING RULE ROWS (Business Hours, After Hours, Custom)
        add_row(f"{cat_prefix}Routing", "Business Hours (In-Hours)", f"Initiate call during Open Hours: [{bh_str}].", f"Call follows standard Active routing for {c_name}.")
        
        zero_out_target = "Operator/Default"
        
        for rule in answering_rules.get('records', []):
            if not rule.get('enabled', False): continue
            r_type = rule.get('type')
            
            # Extract After Hours
            if r_type == 'AfterHours':
                ah_action = rule.get('callHandlingAction', 'Unknown')
                ah_name, ah_id = resolve_target(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), directory)
                add_row(f"{cat_prefix}Routing", "After Hours (Out-of-Hours)", f"Initiate call OUTSIDE of Open Hours: [{bh_str}].", f"Executes [{ah_action}] -> Routes exactly to: {ah_name}.")
                if ah_id and directory.get(ah_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": ah_id, "name": directory[ah_id]['name'], "ext": directory[ah_id]['ext'], "type": directory[ah_id]['type'], "context": f"After Hours Overflow from {c_name}"})
            
            # Extract Custom Rules
            elif r_type == 'Custom':
                c_name_rule = rule.get('name', 'Custom Rule')
                c_cond = parse_custom_rule_triggers(rule)
                c_action = rule.get('callHandlingAction', 'Unknown')
                c_name_target, c_id_target = resolve_target(rule.get('transfer') or rule.get('voicemail') or rule.get('unconditionalForwarding'), directory)
                add_row(f"{cat_prefix}Routing", f"Custom Rule: {c_name_rule}", f"Initiate a call where: {c_cond}.", f"Rule intercepts call. Executes [{c_action}] -> Routes to: {c_name_target}.")
                if c_id_target and directory.get(c_id_target, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": c_id_target, "name": directory[c_id_target]['name'], "ext": directory[c_id_target]['ext'], "type": directory[c_id_target]['type'], "context": f"Custom Rule '{c_name_rule}' from {c_name}"})
            
            # Extract Zero-Out target (Derived from BusinessHours Voicemail Recipient)
            elif r_type == 'BusinessHours' and c_type == 'Department':
                vm_obj = rule.get('voicemail', {}).get('recipient')
                if vm_obj:
                    v_name, _ = resolve_target({'recipient': vm_obj}, directory)
                    zero_out_target = v_name

        # 4. CALL QUEUE SPECIFIC ROWS
        if c_type == 'Department':
            q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/call-queue-info', method='GET', raise_error=False) or {}
            
            t_mode = q_info.get('transferMode', 'Simultaneous')
            agent_timeout = q_info.get('agentTimeout', 15)
            wrap_up = q_info.get('wrapUpTime', 0)
            hold_time = q_info.get('holdTime', 0)
            max_callers = q_info.get('maxCallers', 0)
            int_mode = q_info.get('holdAudioInterruptionMode', 'Never')
            int_period = q_info.get('holdAudioInterruptionPeriod', 0)
            
            hold_action = q_info.get('holdTimeExpirationAction', 'Unknown')
            hold_name, hold_target_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), directory)
            
            max_action = q_info.get('maxCallersAction', 'Unknown')
            max_name, max_target_id = resolve_target(q_info.get('transfer') or q_info.get('voicemail') or q_info.get('unconditionalForwarding'), directory)

            # Greetings & Audio
            if greetings['intro']:
                add_row(f"{cat_prefix}Queue Config", "Introductory Greeting", f"Place call to {c_name}.", "Configured Intro Greeting plays fully before ringing begins.")
            add_row(f"{cat_prefix}Queue Config", "Hold Music", f"Remain in queue.", "Configured connecting audio plays cleanly.")
            if int_mode != 'Never' and int_period > 0:
                add_row(f"{cat_prefix}Queue Config", f"Interrupt Audio ({int_period}s)", f"Remain on hold for at least {int_period + 5}s.", f"At exactly {int_period}s, music pauses, interrupt prompt plays, music resumes.")

            # Distribution Logic
            add_row(f"{cat_prefix}Queue Config", f"Distribution: {t_mode}", f"Ensure multiple agents are 'Available'. Place call.", f"Call distributes based on {t_mode} logic.")
            if t_mode != 'Simultaneous':
                add_row(f"{cat_prefix}Queue Config", f"Agent Ring Timeout ({agent_timeout}s)", f"Targeted agent ignores call for {agent_timeout}s.", f"Timer expires. Call immediately drops from Agent 1 and rings next available.")
            
            if wrap_up > 0:
                add_row(f"{cat_prefix}Queue Config", f"Wrap-Up Timer ({wrap_up}s)", f"Agent completes a queue call. Immediately place a second call.", f"Agent enters Wrap-Up and does NOT receive call until {wrap_up}s expires.")

            # Overflows & Limits (The Crucial Data)
            add_row(f"{cat_prefix}Overflows", "Zero Agents Logged In", f"Log ALL agents out of {c_name}. Initiate call.", f"Executes Max Wait Time Overflow -> {hold_name}.")
            
            if hold_time > 0:
                add_row(f"{cat_prefix}Overflows", f"Max Wait Time Limit ({hold_time}s)", f"Remain on hold in {c_name} until {hold_time}s limit is reached.", f"Timer expires. Executes [{hold_action}] -> {hold_name}.")
                if hold_target_id and directory.get(hold_target_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": hold_target_id, "name": directory[hold_target_id]['name'], "ext": directory[hold_target_id]['ext'], "type": directory[hold_target_id]['type'], "context": f"Wait Time Overflow from {c_name}"})
            else:
                add_row(f"{cat_prefix}Overflows", "Unlimited Wait Time", f"Remain on hold in {c_name} for 5+ minutes.", "No hold limit configured. Call remains in queue indefinitely.")

            if max_callers > 0:
                add_row(f"{cat_prefix}Overflows", f"Max Callers Limit ({max_callers})", f"Simultaneously hold {max_callers} calls in {c_name}. Place call #{max_callers + 1}.", f"Call #{max_callers + 1} instantly executes [{max_action}] -> {max_name}.")
                if max_target_id and directory.get(max_target_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": max_target_id, "name": directory[max_target_id]['name'], "ext": directory[max_target_id]['ext'], "type": directory[max_target_id]['type'], "context": f"Max Callers Overflow from {c_name}"})
            else:
                add_row(f"{cat_prefix}Overflows", "Unlimited Queue Capacity", f"Place multiple concurrent calls into {c_name}.", "No maximum caller limit configured. Calls enter queue normally.")

            add_row(f"{cat_prefix}Overflows", "Zero-Out (DTMF Escape)", f"While listening to {c_name} hold music, press '0'.", f"Call escapes queue and routes to -> {zero_out_target}.")

        # 5. IVR SPECIFIC ROWS
        elif c_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{c_id}', method='GET', raise_error=False) or {}
            
            if 'prompt' in ivr_info:
                p_text = ivr_info['prompt'].get('text', 'Audio File')
                add_row(f"{cat_prefix}IVR Prompts", "Greeting Playback", f"Dial {c_name}.", f"Prompt plays clearly: '{p_text}'.")

            if 'actions' in ivr_info:
                for act in ivr_info['actions']:
                    key = act.get('input', '')
                    if not key: continue 
                    a_type = act.get('action', 'Unknown')
                    t_name, t_id = resolve_target(act, directory)
                    
                    add_row(f"{cat_prefix}IVR Routing", f"Key Mapping: Press '{key}'", f"Listen to prompt and press '{key}'.", f"Executes [{a_type}] -> {t_name}.")
                    
                    if t_id and directory.get(t_id, {}).get('type') in ['Department', 'IvrMenu']:
                        nodes_to_process.append({"id": t_id, "name": directory[t_id]['name'], "ext": directory[t_id]['ext'], "type": directory[t_id]['type'], "context": f"Key '{key}' from {c_name}"})

            add_row(f"{cat_prefix}IVR Boundaries", "Invalid Key Press", f"Press an unassigned key in {c_name}.", "System plays 'Invalid entry' prompt and replays menu.")

    return uat_cases
