# webapp/visualiser/utils.py
from datetime import datetime
from webapp.rc_api import rc_api_call

# --- Visualiser Specific Helpers ---
extension_cache = {}

def get_extension_info(ext_id):
    """Helper to get full extension info with caching."""
    if ext_id in extension_cache: return extension_cache[ext_id]
    info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
    if info: extension_cache[ext_id] = info
    return info

def get_queue_members_info(ext_id):
    """Fetches and formats queue member names for display."""
    try:
        members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
        if not members_resp or not members_resp.get('records'): return "Members (0)", []
        member_details = []
        for member in members_resp['records']:
            info = get_extension_info(member.get('id')) 
            name = f"{info['contact'].get('firstName', '')} {info['contact'].get('lastName', '')}".strip() if info and info.get('contact') else "Unknown"
            member_details.append(f"{name} (Ext: {member.get('extensionNumber', 'N/A')})")
        return f"Queue Members ({len(member_details)})", member_details
    except Exception:
        return "Members: ERROR", []

def get_business_hours_summary(ext_id):
    """Fetches and formats opening hours as a summary string."""
    try:
        hours_response = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        schedule = hours_response.get('schedule')
        if not schedule or not schedule.get('weeklyRanges'): return "Hours: 24/7"
        weekly_ranges = schedule.get('weeklyRanges', {})
        days_active = [day.capitalize() for day in weekly_ranges if weekly_ranges[day]]
        if not days_active: return "Hours: Closed (All Week)"
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
             if day in weekly_ranges and weekly_ranges[day]:
                    time_from, time_to = weekly_ranges[day][0]['from'], weekly_ranges[day][0]['to']
                    return f"Hours: {', '.join(days_active)} {time_from} - {time_to}"
        return "Hours: Custom Schedule"
    except Exception: return "Hours: Runtime ERROR"

def parse_rule_details(detailed_rule):
    """Parses rule details into action, schedule, and target."""
    try:
        schedule_details, call_action, action_target = "N/A", "N/A", "N/A"
        schedule_obj = detailed_rule.get('schedule', {})
        
        if 'ranges' in schedule_obj and schedule_obj.get('ranges'):
            first_range = schedule_obj['ranges'][0]
            date_from = datetime.fromisoformat(first_range.get('from').replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
            date_to = datetime.fromisoformat(first_range.get('to').replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
            schedule_details = f"Date Range: {date_from} to {date_to}"
        elif 'weeklyRanges' in schedule_obj and any(schedule_obj['weeklyRanges'].values()):
            schedule_details = "Custom weekly schedule"
        elif 'ref' in schedule_obj:
            schedule_details = detailed_rule.get('name') or schedule_obj['ref']

        call_action = detailed_rule.get('callHandlingAction', 'N/A')
        
        if call_action == 'TakeMessagesOnly':
            recipient = detailed_rule.get('voicemail', {}).get('recipient', {})
            action_target = f"Voicemail Box ID {recipient.get('id', 'N/A')}"
            call_action = "Voicemail"
        elif call_action in ['TransferToExtension', 'ForwardCalls']:
            transfer_ext_id = detailed_rule.get('transfer', {}).get('extension', {}).get('id')
            if transfer_ext_id:
                info = get_extension_info(transfer_ext_id)
                action_target = f"Ext: {info.get('extensionNumber', 'N/A')}" if info else f"ID: {transfer_ext_id}"
                call_action = "TransferToExtension"
            elif 'forwarding' in detailed_rule:
                rules = detailed_rule.get('forwarding', {}).get('rules', [])
                if rules and rules[0].get('forwardingNumbers'):
                    target = rules[0]['forwardingNumbers'][0]
                    if 'phoneNumber' in target:
                        action_target = target['phoneNumber']
                        call_action = "UnconditionalForwarding"
                    elif 'extension' in target:
                        info = get_extension_info(target['extension'].get('id'))
                        action_target = f"Ext: {info.get('extensionNumber', 'N/A')}" if info else "Unknown Ext"
                        call_action = "TransferToExtension"
        elif call_action == 'PlayAnnouncementOnly':
             action_target = "N/A"
        
        return schedule_details, call_action, action_target
    except Exception:
        return "Rule Details: ERROR", "Action: ERROR", "Target: ERROR"

def trace_flow_recursive(ext_id, node_counter, flow_data, processed_extensions):
    """Recursively traces the entire call flow path for any extension type."""
    if ext_id in processed_extensions or node_counter > 20:
        return node_counter, flow_data
    
    # Special case for the "Main Company Number"
    if node_counter == 1 and 'maincompanynumber' in flow_data[0].get('details', [''])[0].lower():
        main_number_info = rc_api_call("/restapi/v1.0/account/~/business-address")
        if operator_id := main_number_info.get('operator', {}).get('id'):
            ext_id = operator_id

    ext_info = get_extension_info(ext_id)
    if not ext_info:
        flow_data.append({'type': 'endpoint', 'name': 'End of Call Flow', 'details': [f"Could not trace ID: {ext_id}"]})
        return node_counter + 1, flow_data

    processed_extensions[ext_id] = ext_id
    ext_type = ext_info.get('type', 'Unknown')
    ext_name = ext_info.get('name', 'Unknown Extension')
    ext_number = ext_info.get('extensionNumber', 'N/A')
    
    main_node_details = [f"Type: {ext_type} (Ext: {ext_number})"]
    current_node_data = {
        'id': f"N{node_counter}", 'type': 'queue', 'name': ext_name, 
        'details': main_node_details, 'rules': [], 'members': [], 
        'members_name': '', 'branches': []
    }
    flow_data.append(current_node_data)
    node_counter += 1

    # Helper function to recursively trace a rule's destination
    def _trace_rule_destination(rule, rule_name, counter):
        branch_data = []
        schedule, action, target = parse_rule_details(rule)
        details = [f"<b>Schedule:</b> {schedule or rule_name}", f"<b>Action:</b> {action} → {target}"]
        branch_data.append({'id': f"N{counter}", 'type': 'queue', 'name': rule_name, 'details': details, 'branches': []})
        counter += 1
        
        next_ext_id = None
        if 'TransferToExtension' in action:
            if transfer := rule.get('transfer'):
                next_ext_id = transfer.get('extension', {}).get('id')
            elif forwarding := rule.get('forwarding'):
                 if numbers := forwarding.get('rules', [{}])[0].get('forwardingNumbers'):
                        if numbers and 'extension' in numbers[0]:
                                next_ext_id = numbers[0].get('extension', {}).get('id')
        
        if next_ext_id:
            counter, sub_branches = trace_flow_recursive(next_ext_id, counter, [], processed_extensions)
            branch_data.extend(sub_branches)
        
        return counter, branch_data

    # --- Main Logic ---
    rules_endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
    rules_summary = rc_api_call(rules_endpoint)
    
    inactive_rules_display = []
    active_custom_rule = None

    if rules_summary:
        for rule_summary in rules_summary.get('records', []):
            if rule_summary.get('type') == 'Custom':
                detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_summary['id']}")
                if detailed_rule:
                    if detailed_rule.get('enabled'):
                        active_custom_rule = detailed_rule
                    else:
                        # Collect inactive rules for display on the side
                        schedule, action, target = parse_rule_details(detailed_rule)
                        inactive_rules_display.append(f"<b>{detailed_rule.get('name')} (Inactive):</b><br/>{action} → {target}")

    current_node_data['rules'] = inactive_rules_display

    if active_custom_rule:
        current_node_data['details'].append(f"<b>Override:</b> {active_custom_rule.get('name')} (Active)")
        node_counter, new_branch = _trace_rule_destination(active_custom_rule, f"Active Rule: {active_custom_rule.get('name')}", node_counter)
        current_node_data['branches'].append(new_branch)
    else:
        # Trace both Business Hours and After Hours paths
        if bh_rule := rc_api_call(f"{rules_endpoint}/business-hours-rule"):
            node_counter, new_branch = _trace_rule_destination(bh_rule, "Business Hours", node_counter)
            current_node_data['branches'].append(new_branch)
        if ah_rule := rc_api_call(f"{rules_endpoint}/after-hours-rule"):
            if ah_rule.get('enabled'):
                node_counter, new_branch = _trace_rule_destination(ah_rule, "After Hours", node_counter)
                current_node_data['branches'].append(new_branch)

    # Add extension-specific details
    if ext_type == 'IvrMenu':
        ivr_keys = [f"<b>Key {a.get('input')}</b> → Ext: {get_extension_info(a.get('extension', {}).get('id')).get('extensionNumber')}" for p in ext_info.get('prompts', []) for a in p.get('actions', []) if a.get('action') == 'Connect']
        current_node_data['rules'].extend(ivr_keys) # Add IVR keys to the side panel
        # Also trace each IVR keypress as a separate branch
        for p in ext_info.get('prompts', []):
            for a in p.get('actions', []):
                if a.get('action') == 'Connect' and (next_id := a.get('extension', {}).get('id')):
                    ext_num = get_extension_info(next_id).get('extensionNumber')
                    node_counter, new_branch = _trace_rule_destination({'callHandlingAction': 'TransferToExtension', 'transfer': {'extension': {'id': next_id}}}, f"Key '{a.get('input')}' → Ext {ext_num}", node_counter)
                    current_node_data['branches'].append(new_branch)

    elif members_resp := rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members"):
        name, members = get_queue_members_info(ext_id)
        current_node_data['members'], current_node_data['members_name'] = members, name
        if bh_rule := rc_api_call(f"{rules_endpoint}/business-hours-rule"):
            if bh_rule.get('holdTimeExpirationAction') == 'TransferToExtension':
                 node_counter, new_branch = _trace_rule_destination(bh_rule, "Queue Overflow", node_counter)
                 current_node_data['branches'].append(new_branch)

    return node_counter, flow_data