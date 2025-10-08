import os
import secrets
import base64
import hashlib
import time
import requests
from flask import session, current_app
from google.cloud import firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from datetime import datetime

# Database initialization
try:
    db = firestore.Client()
except Exception as e:
    print(f"WARNING: Could not initialize Firestore client: {e}. Check credentials.")

# --- CONSTANTS ---
PASSCODE_COLLECTION_ID = 'RCAU_APITOOLS_WEBAPP_PASSCODE'
PASSCODE_DOCUMENT_ID = 'passcode'
PASSCODE_FIELD = 'app_passcode'
ADMIN_LIST_FIELD = 'admin_emails'

# --- Session & Auth Helpers ---
def is_authenticated() -> bool:
    """Checks for a successful website login session (Layer 1)."""
    return session.get('authenticated', False) and session.get('user_email') is not None

def is_admin_user() -> bool:
    """Checks if the currently logged-in website user has admin privileges."""
    return session.get('is_admin', False)

def get_rc_access_token() -> str | None:
    """Retrieves the user's dynamic RingCentral token from the session (Layer 2)."""
    return session.get('rc_access_token')

def create_pkce_challenge():
    """Standard, robust PKCE challenge generation."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(hashed).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge

# --- Firestore Helpers ---
def get_config_from_firestore() -> dict | None:
    """Retrieves the shared passcode and admin list from Firestore."""
    try:
        doc_ref: DocumentSnapshot = db.collection(PASSCODE_COLLECTION_ID).document(PASSCODE_DOCUMENT_ID).get()
        if doc_ref.exists:
            data = doc_ref.to_dict()
            return {'passcode': data.get(PASSCODE_FIELD), 'admin_list': data.get(ADMIN_LIST_FIELD, [])}
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to access Firestore: {e}")
    return None

# --- RingCentral API Call Helpers ---
extension_cache = {}

def rc_api_call(endpoint, method="GET", body=None, params=None) -> dict | None:
    """Makes a generic, authenticated call to the RingCentral API with session logging."""
    rc_token = get_rc_access_token()
    
    if 'api_log' not in session:
        session['api_log'] = []

    if not rc_token:
        session['api_log'].append({'status': 'FAIL', 'endpoint': endpoint, 'detail': 'Token missing'})
        session.modified = True
        return None

    url = f"{current_app.config['RC_SERVER_URL']}{endpoint}"
    headers = {"Authorization": f"Bearer {rc_token}", "Accept": "application/json"}
    start_time = time.time()
    
    try:
        response = requests.request(method.upper(), url, headers=headers, params=params, json=body)
        # Gracefully handle 404 Not Found as a valid "empty" response
        if response.status_code == 404:
            return None
        
        response.raise_for_status()
        duration = (time.time() - start_time) * 1000
        session['api_log'].append({'status': 'SUCCESS', 'endpoint': endpoint, 'code': response.status_code, 'duration': f"{duration:.0f}ms", 'method': method})
        session.modified = True
        return response.json() if response.content else {"status": "success", "content_empty": True}
    except requests.exceptions.RequestException as e:
        duration = (time.time() - start_time) * 1000
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text if e.response is not None else 'No response body'
        # Don't log expected 404s as failures
        if status_code != 404:
            session['api_log'].append({'status': 'FAIL', 'endpoint': endpoint, 'code': status_code, 'duration': f"{duration:.0f}ms", 'method': method, 'detail': response_text[:100]})
            session.modified = True
        return None

# --- Visualiser Specific Helpers ---
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
    
    # Handle the special case where the flow starts with a "Main Company Number"
    # This ID isn't a real extension, so we find the operator (Auto-Receptionist) it points to.
    if node_counter == 1 and 'maincompanynumber' in flow_data[0].get('details', [''])[0].lower():
        main_number_info = rc_api_call("/restapi/v1.0/account/~/business-address")
        if operator_id := main_number_info.get('operator', {}).get('id'):
            ext_id = operator_id

    ext_info = get_extension_info(ext_id)
    if not ext_info:
        flow_data.append({'type': 'endpoint', 'name': 'End of Call Flow', 'details': [f"Could not trace extension ID: {ext_id}"]})
        return node_counter + 1, flow_data

    processed_extensions[ext_id] = ext_id
    ext_type = ext_info.get('type', 'Unknown')
    ext_name = ext_info.get('name', f'ID {ext_id}')
    ext_number = ext_info.get('extensionNumber', 'N/A')
    
    # --- Base Node Creation ---
    main_node_details = [f"Type: {ext_type} (Ext: {ext_number})"]
    current_node_data = {'id': f"N{node_counter}", 'type': 'queue', 'name': ext_name, 'details': main_node_details, 'rules': [], 'members': [], 'members_name': ''}
    flow_data.append(current_node_data)
    node_counter += 1
    next_ext_id = None

    # --- Answering Rules Logic (Applies to most extension types) ---
    rules_endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
    rules_summary = rc_api_call(rules_endpoint)
    active_custom_rule = None
    
    # First, find if there is an ACTIVE custom rule, as it takes precedence
    if rules_summary:
        for rule_summary in rules_summary.get('records', []):
            if rule_summary.get('enabled') and rule_summary.get('type') == 'Custom':
                active_custom_rule = rc_api_call(f"{rules_endpoint}/{rule_summary['id']}")
                break # Found the active one, no need to look further

    # If an active custom rule is found, trace it
    if active_custom_rule:
        current_node_data['details'].append(f"<b>Override:</b> {active_custom_rule.get('name')} (Active)")
        schedule, action, target = parse_rule_details(active_custom_rule)
        details = [f"<b>Schedule:</b> {schedule}", f"<b>Action:</b> {action} → {target}"]
        flow_data.append({'id': f"N{node_counter}", 'type': 'queue', 'name': 'Active Custom Rule', 'details': details})
        node_counter += 1
        # If the rule transfers to another extension, that's our next trace path
        if 'TransferToExtension' in action:
            next_ext_id = (active_custom_rule.get('transfer', {}) or active_custom_rule.get('forwarding', {}).get('rules', [{}])[0].get('forwardingNumbers', [{}])[0].get('extension', {})).get('id')

    # If NO active custom rule, trace the standard Business Hours / After Hours rules
    else:
        # Trace Business Hours Rule
        business_hours_rule = rc_api_call(f"{rules_endpoint}/business-hours-rule")
        if business_hours_rule:
            schedule, action, target = parse_rule_details(business_hours_rule)
            details = [f"<b>Schedule:</b> Business Hours", f"<b>Action:</b> {action} → {target}"]
            flow_data.append({'id': f"N{node_counter}", 'type': 'queue', 'name': 'Business Hours Action', 'details': details})
            node_counter += 1
            if 'TransferToExtension' in action:
                next_ext_id = (business_hours_rule.get('transfer', {}) or {}).get('extension', {}).get('id')
        
        # Trace After Hours Rule
        after_hours_rule = rc_api_call(f"{rules_endpoint}/after-hours-rule")
        if after_hours_rule and after_hours_rule.get('enabled'):
            schedule, action, target = parse_rule_details(after_hours_rule)
            details = [f"<b>Schedule:</b> After Hours", f"<b>Action:</b> {action} → {target}"]
            flow_data.append({'id': f"N{node_counter}", 'type': 'endpoint', 'name': 'After Hours Action', 'details': details})
            node_counter += 1
            # After-hours rules can also point to another extension to trace
            if 'TransferToExtension' in action:
                # If we don't already have a path from business hours, use this one
                if not next_ext_id:
                    next_ext_id = (after_hours_rule.get('transfer', {}) or {}).get('extension', {}).get('id')

    # --- Extension-Specific Details (IVR Keys, Queue Members, etc.) ---
    
    # For IVR Menus, add the keypress info
    if ext_type == 'IvrMenu':
        ivr_details = []
        for prompt in ext_info.get('prompts', []):
            for action in prompt.get('actions', []):
                if action.get('action') == 'Connect':
                    key = action.get('input', 'Any')
                    target_ext = action.get('extension', {})
                    if target_id := target_ext.get('id'):
                        target_info = get_extension_info(target_id)
                        target_num = target_info.get('extensionNumber', 'N/A') if target_info else 'N/A'
                        ivr_details.append(f"<b>Key {key}</b> → Ext: {target_num}")
        current_node_data['rules'] = ivr_details # Display keypresses as "rules" on the side

    # For Call Queues, add the member list
    if members_resp := rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members"):
        member_name, member_list = get_queue_members_info(ext_id)
        current_node_data['members'], current_node_data['members_name'] = member_list, member_name

    # --- Recursive Call ---
    # If any of the steps above found a destination extension, trace it now
    if next_ext_id and next_ext_id not in processed_extensions:
        return trace_flow_recursive(next_ext_id, node_counter, flow_data, processed_extensions)

    return node_counter, flow_data
