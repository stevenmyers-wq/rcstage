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
        response.raise_for_status()
        duration = (time.time() - start_time) * 1000
        session['api_log'].append({'status': 'SUCCESS', 'endpoint': endpoint, 'code': response.status_code, 'duration': f"{duration:.0f}ms", 'method': method})
        session.modified = True
        return response.json() if response.content else {"status": "success", "content_empty": True}
    except requests.exceptions.RequestException as e:
        duration = (time.time() - start_time) * 1000
        status_code = e.response.status_code if e.response is not None else 'N/A'
        response_text = e.response.text if e.response is not None else 'No response body'
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
    """Recursively traces the call flow and generates data blocks for the renderer."""
    if ext_id in processed_extensions or node_counter > 20:
        return node_counter, flow_data
        
    ext_info = get_extension_info(ext_id)
    if not ext_info:
        flow_data.append({'type': 'error', 'name': f'Error fetching Ext {ext_id}', 'details': []})
        return node_counter + 1, flow_data

    processed_extensions[ext_id] = ext_id 
    ext_type = ext_info.get('type', 'Unknown')
    ext_name = ext_info.get('name', f'ID {ext_id}')
    ext_number = ext_info.get('extensionNumber', 'N/A')
    
    rules_endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
    rules_summary = rc_api_call(rules_endpoint)
    active_custom_rule, rule_details_list = None, []
    
    if rules_summary and rules_summary.get('records'):
        for rule_summary in rules_summary.get('records', []):
            if rule_summary.get('type') == 'Custom':
                 detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_summary['id']}")
                 if detailed_rule:
                     is_enabled = detailed_rule.get('enabled', False)
                     schedule_details, call_action, action_target = parse_rule_details(detailed_rule)
                     rule_details_list.append({
                         'name': detailed_rule.get('name', 'Custom Rule'),
                         'active': is_enabled,
                         'details': [f"<b>Schedule:</b> {schedule_details}", f"<b>Action:</b> {call_action} → {action_target}"]
                     })
                     if is_enabled:
                         active_custom_rule = detailed_rule
                         
    main_node_details = [f"Type: {ext_type} (Ext: {ext_number})"]
    if active_custom_rule:
        main_node_details.append(f"<b>Override:</b> {active_custom_rule.get('name')} (Active)")
    
    current_node_data = {'id': f"N{node_counter}",'type': 'queue','name': ext_name,'details': main_node_details,'rules': rule_details_list,'members': [],'members_name': '','next_ext_id': None}
    flow_data.append(current_node_data)
    next_node_counter, next_ext_id = node_counter + 1, None
    
    bh_rule = rc_api_call(f"{rules_endpoint}/business-hours-rule")
    members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")

    # --- Call Queue Logic (as before) ---
    if members_resp is not None:
        flow_data[-1]['details'].append(get_business_hours_summary(ext_id))
        flow_data[-1]['details'].append(f"<b>Ring Type:</b> {bh_rule.get('queue', {}).get('transferMode', 'N/A')}")
        member_name, member_list = get_queue_members_info(ext_id)
        flow_data[-1]['members'], flow_data[-1]['members_name'] = member_list, member_name

        if bh_rule:
            queue_details = bh_rule.get('queue', {})
            transfer_ext = bh_rule.get('transfer', {}).get('extension', {})
            overflow_details = [
                f"<b>Agents ring for:</b> {queue_details.get('agentTimeout', 'N/A')} seconds.",
                f"<b>Queue Full ({queue_details.get('maxCallers', 'N/A')} callers):</b> {queue_details.get('maxCallersAction', 'N/A')}",
                f"<b>Wait Timeout ({queue_details.get('holdTime', 'N/A')}s):</b> {queue_details.get('holdTimeExpirationAction', 'N/A')}"
            ]
            flow_data.append({'id': f"N{next_node_counter}",'type': 'queue','name': 'Overflow Rules','details': overflow_details})
            next_node_counter += 1
            if queue_details.get('holdTimeExpirationAction') == 'TransferToExtension' and transfer_ext.get('id'):
                next_ext_id = transfer_ext['id']
                
        after_hours_rule = rc_api_call(f"{rules_endpoint}/after-hours-rule")
        if after_hours_rule and after_hours_rule.get('enabled'):
             schedule_details, call_action, action_target = parse_rule_details(after_hours_rule)
             flow_data.append({'id': f"N{next_node_counter}",'type': 'endpoint','name': 'After Hours Action','details': [f"<b>Action:</b> {call_action} → {action_target}"]})
             next_node_counter += 1
             
    # --- NEW: Logic for 'Site' extensions ---
    elif ext_type == 'Site':
        site_business_hours_rule = rc_api_call(f"/restapi/v1.0/account/~/sites/{ext_id}/ivrs/0/answering-rules/business-hours-rule")
        if site_business_hours_rule:
             schedule_details, call_action, action_target = parse_rule_details(site_business_hours_rule)
             flow_data.append({'id': f"N{next_node_counter}",'type': 'queue','name': 'Business Hours Action','details': [f"<b>Action:</b> {call_action} → {action_target}"]})
             next_node_counter += 1
             # Check if the target is another extension we need to trace
             if call_action == 'TransferToExtension':
                 transfer_ext_id = site_business_hours_rule.get('transfer', {}).get('extension', {}).get('id')
                 if transfer_ext_id:
                     next_ext_id = transfer_ext_id

    # --- FIX: Corrected Logic for 'IvrMenu' extensions ---
    elif ext_type == 'IvrMenu':
        ivr_details = []
        next_ext_id_from_ivr = None
        # The keypress data is in the 'actions' part of the main extension info
        for action in ext_info.get('actions', []):
            if action.get('action') == 'Connect':
                key = action.get('input', 'Any')
                target_ext = action.get('extension', {})
                if target_ext.get('id'):
                    # Fetching info just to get the extension number for display
                    target_info = get_extension_info(target_ext['id'])
                    target_ext_num = target_info.get('extensionNumber', 'N/A') if target_info else 'N/A'
                    ivr_details.append(f"<b>Key {key}</b> → Ext: {target_ext_num}")
                    # Capture the first keypress destination to trace next
                    if not next_ext_id_from_ivr:
                        next_ext_id_from_ivr = target_ext['id']
        
        # Add the IVR Keypresses box even if it's empty, to show the end of a path
        flow_data.append({'id': f"N{next_node_counter}",'type': 'queue','name': 'IVR Keypresses','details': ivr_details if ivr_details else ['No keypress actions defined.']})
        next_node_counter += 1
        # Set the next extension to trace
        next_ext_id = next_ext_id_from_ivr

    if next_ext_id and next_ext_id not in processed_extensions:
        return trace_flow_recursive(next_ext_id, next_node_counter, flow_data, processed_extensions)

    return next_node_counter, flow_data
