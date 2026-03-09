from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, Sites, and Shared Lines for testing. EXCLUDES standard Users."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        return []

    # Focus purely on Call Flow elements
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
    """Helper to translate raw API actions into human-readable expected results."""
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
        return "Call routes to configured ringing members/devices."
    elif action == 'Bypass':
        return "Call bypasses normal routing (usually direct to Voicemail)."
    return f"Call follows routing behavior: {action}."

def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Crawls answering rules, IVR menus, and Queue settings to build exhaustive UATs."""
    uat_cases = []
    
    # 1. Base Connectivity Test
    uat_cases.append({
        "scenario": "Base Connectivity",
        "step": f"Dial {extension_number} ({extension_name}) from an external number.",
        "action": "Listen to initial connection.",
        "expected": "Call connects successfully without dead air, immediate drops, or SIP 4xx/5xx errors."
    })

    # 2. Fetch and parse standard Answering Rules
    rules_response = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=False)
    
    if rules_response and 'records' in rules_response:
        # Sort rules: Custom first, then Business Hours, then After Hours
        sorted_rules = sorted(rules_response['records'], key=lambda x: 0 if x.get('type') == 'Custom' else 1 if x.get('type') == 'BusinessHours' else 2)
        
        for rule in sorted_rules:
            if not rule.get('enabled', False):
                continue
                
            rule_type = rule.get('type')
            action = rule.get('callHandlingAction', 'Unknown')
            expected = format_action_string(action, rule)

            if rule_type == 'Custom':
                name = rule.get('name', 'Custom Rule')
                callers = rule.get('callers', [])
                caller_str = f"Specific Caller IDs ({len(callers)} configured)" if callers else "Specific conditions"
                
                uat_cases.append({
                    "scenario": f"Custom Rule: {name}",
                    "step": f"Trigger rule conditions: {caller_str}.",
                    "action": "Wait for routing.",
                    "expected": expected
                })
            
            elif rule_type == 'BusinessHours':
                uat_cases.append({
                    "scenario": "Business Hours Routing",
                    "step": "Call during configured Open Hours.",
                    "action": "Wait for routing.",
                    "expected": expected
                })
                
            elif rule_type == 'AfterHours':
                uat_cases.append({
                    "scenario": "After Hours Routing",
                    "step": "Call outside of configured Open Hours.",
                    "action": "Wait for routing.",
                    "expected": expected
                })

    # 3. Exhaustive Crawl: Call Queues (Departments)
    if extension_type == 'Department':
        # Fetch deep queue info
        q_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/call-queue-info', method='GET', raise_error=False)
        if q_info:
            # Check hold times and overflows
            max_wait_time = q_info.get('maxCallersAction', 'Unknown')
            uat_cases.append({
                "scenario": "Queue Wait Time Limit",
                "step": "Call queue and remain on hold until max wait time is reached.",
                "action": "Do not answer the call as an agent.",
                "expected": "Call triggers Primary Overflow action. Verify hold music stops and overflow destination is reached."
            })
            uat_cases.append({
                "scenario": "Queue Maximum Callers",
                "step": "Flood the queue with concurrent test calls up to the maximum limit.",
                "action": "Place one additional call over the limit.",
                "expected": f"The final call triggers the 'Max Callers' overflow action."
            })
            uat_cases.append({
                "scenario": "Queue Zero-Out",
                "step": "Call queue, and while on hold listening to music/prompts, press '0'.",
                "action": "Press 0 on dialpad.",
                "expected": "If zero-out is configured, call escapes queue and goes to designated operator. Otherwise, system ignores input."
            })

    # 4. Exhaustive Crawl: IVR Menus
    if extension_type == 'IvrMenu':
        ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{extension_id}', method='GET', raise_error=False)
        if ivr_info and 'actions' in ivr_info:
            for action in ivr_info['actions']:
                key = action.get('input', 'Unknown')
                act_type = action.get('action', 'Unknown')
                target = action.get('extension', {}).get('id', 'Unknown') if act_type == 'Transfer' else 'N/A'
                
                expected_str = f"Call transfers to Extension ID {target}." if act_type == 'Transfer' else f"Triggers {act_type} logic."
                
                uat_cases.append({
                    "scenario": "IVR Key Press Mapping",
                    "step": f"When listening to the IVR prompt, press '{key}'.",
                    "action": "Press key on dialpad.",
                    "expected": expected_str
                })
            
            # Add implicit IVR tests
            uat_cases.append({
                "scenario": "IVR Invalid Input",
                "step": "When listening to the IVR prompt, press an unassigned key (e.g., '9' or '#').",
                "action": "Press invalid key.",
                "expected": "System plays 'Invalid entry' prompt and replays the menu."
            })
            uat_cases.append({
                "scenario": "IVR Timeout (No Input)",
                "step": "Listen to the IVR prompt and press nothing.",
                "action": "Wait for timeout (typically 3 loops).",
                "expected": "System executes the default timeout action (usually disconnect or transfer to operator)."
            })

    # 5. Wrap up
    uat_cases.append({
        "scenario": "Call Termination",
        "step": "During any active connected state, caller hangs up.",
        "action": "End call from origin device.",
        "expected": "Call drops immediately from the system. If testing a queue, the agent is returned to Available status."
    })

    return uat_cases
