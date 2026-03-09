# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches Call Queues, IVR Menus, and main extensions for testing."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 500}, raise_error=True)
    if not response or 'records' not in response:
        return []

    valid_types = ['Department', 'IvrMenu', 'User', 'SharedLinesGroup']
    
    entities = [
        {
            "id": ext['id'],
            "name": ext.get('name', 'Unnamed'),
            "extensionNumber": ext.get('extensionNumber', 'N/A'),
            "type": ext['type']
        }
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    
    # Sort alphabetically for the dropdown
    return sorted(entities, key=lambda x: x['name'])

def generate_uat_cases(extension_id, extension_name, extension_number):
    """
    Crawls the answering rules of an extension and generates UAT cases.
    """
    rules_response = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=True)
    
    if not rules_response or 'records' not in rules_response:
        raise Exception("Failed to fetch answering rules.")

    uat_cases = []
    
    # Base Test Case: Dial the number
    uat_cases.append({
        "step": f"Dial extension {extension_number} ({extension_name})",
        "action": "Initiate Call",
        "expected": "Call connects without dead air or immediate drop."
    })

    # Crawl the rules
    for rule in rules_response['records']:
        rule_type = rule.get('type', 'Unknown')
        rule_name = rule.get('name', rule_type)
        enabled = rule.get('enabled', False)
        
        if not enabled:
            continue
            
        action = rule.get('callHandlingAction', 'Unknown')
        
        # Translate the API logic into a human-readable Test Case
        condition = "During Business Hours" if rule_type == 'BusinessHours' else "During After Hours" if rule_type == 'AfterHours' else f"When rule '{rule_name}' applies"
        
        if action == 'TransferToExtension':
            target_ext = rule.get('transfer', {}).get('extension', {}).get('id', 'Unknown')
            expected = f"Call is transferred to internal extension (ID: {target_ext})."
        elif action == 'TakeMessagesReturnToGreeting':
            expected = "Call goes to Voicemail. Verify the correct VM greeting plays."
        elif action == 'PlayAnnouncementOnly':
            expected = "An announcement plays, and then the call is automatically disconnected."
        elif action == 'UnconditionalForwarding':
            expected = "Call is immediately forwarded to an external number."
        elif action == 'ForwardCalls':
            expected = "Call rings sequentially or simultaneously to configured forwarding numbers."
        elif action == 'TransferToExternalNumber':
            expected = "Call transfers to an external phone number."
        else:
            expected = f"Call follows routing behavior: {action}."

        uat_cases.append({
            "step": f"Test routing condition: {condition}",
            "action": "Wait for call routing",
            "expected": expected
        })
        
        # If it's a queue/department, add a standard hold time test
        if action == 'ForwardCalls' and 'Department' in extension_name: # Simple heuristic
             uat_cases.append({
                "step": "Remain in queue",
                "action": "Do not answer the call immediately",
                "expected": "Hold music plays. Verify max wait time behavior triggers (e.g. overflow to VM)."
            })

    # Wrapup test case
    uat_cases.append({
        "step": "End the call",
        "action": "Caller hangs up",
        "expected": "Call disconnects cleanly. Any voicemails left are delivered to the correct inbox."
    })

    return uat_cases
