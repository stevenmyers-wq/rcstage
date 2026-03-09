# webapp/ringex_uat/utils.py
from webapp.rc_api import rc_api_call

def get_testable_extensions():
    """Fetches base call flows for the UI dropdown."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response: return []
    valid_types = ['Department', 'IvrMenu', 'SharedLinesGroup', 'Site']
    entities = [{"id": ext['id'], "name": ext.get('name', 'Unnamed'), "extensionNumber": ext.get('extensionNumber', 'N/A'), "type": ext['type']} for ext in response['records'] if ext.get('type') in valid_types]
    return sorted(entities, key=lambda x: x['name'])

def build_extension_map():
    """Builds a global directory to resolve IDs to Names."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 2000}, raise_error=False)
    ext_map = {}
    if response and 'records' in response:
        for ext in response['records']:
            ext_map[str(ext['id'])] = {'name': ext.get('name', 'Unknown'), 'ext': ext.get('extensionNumber', 'N/A'), 'type': ext.get('type', 'Unknown')}
    return ext_map

def get_direct_numbers(ext_id):
    """Strictly matches DIDs to the specific extension."""
    response = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/phone-number', method='GET', raise_error=False)
    dids = []
    if response and 'records' in response:
        for r in response['records']:
            if r.get('usageType') == 'DirectNumber' and str(r.get('extension', {}).get('id', '')) == str(ext_id):
                dids.append(r.get('phoneNumber'))
    return dids

def resolve_rule_target(rule, ext_map):
    """Resolves standard answering rule targets (Transfers, Forwarding, Voicemail)."""
    if 'transfer' in rule and rule['transfer'].get('extension', {}).get('id'):
        tid = str(rule['transfer']['extension']['id'])
        return f"{ext_map.get(tid, {}).get('name', tid)} (Ext {ext_map.get(tid, {}).get('ext', '')})", tid
    if 'voicemail' in rule and rule['voicemail'].get('recipient', {}).get('id'):
        tid = str(rule['voicemail']['recipient']['id'])
        return f"Voicemail of {ext_map.get(tid, {}).get('name', tid)}", tid
    if 'unconditionalForwarding' in rule:
        return f"External Number: {rule['unconditionalForwarding'].get('phoneNumber')}", None
    return "Default/Operator", None

def resolve_queue_overflow(action_type, rule, ext_map):
    """Resolves Queue-specific overflows based on action strings ('Voicemail', 'Transfer')."""
    if action_type == 'Voicemail':
        tid = str(rule.get('voicemail', {}).get('recipient', {}).get('id', ''))
        return f"Voicemail of {ext_map.get(tid, {}).get('name', tid)}", tid
    if action_type == 'Transfer':
        tid = str(rule.get('transfer', {}).get('extension', {}).get('id', ''))
        return f"{ext_map.get(tid, {}).get('name', tid)} (Ext {ext_map.get(tid, {}).get('ext', '')})", tid
    return str(action_type), None

def parse_custom_conditions(rule):
    """Extracts the exact Caller ID or Called Number that triggers a custom rule."""
    conds = []
    if rule.get('callers'):
        c_ids = [c.get('callerId', c.get('name', 'Unknown')) for c in rule['callers']]
        conds.append(f"Caller ID is {', '.join(c_ids)}")
    if rule.get('calledNumbers'):
        n_ids = [n.get('phoneNumber', 'Unknown') for n in rule['calledNumbers']]
        conds.append(f"Dialed Number is {', '.join(n_ids)}")
    return " AND ".join(conds) if conds else "Specific Schedule/Condition"


def generate_uat_cases(extension_id, extension_name, extension_number, extension_type):
    """Data-driven UAT generator mapping the exact API JSON."""
    uat_cases = []
    case_counter = 1
    ext_map = build_extension_map()
    
    # Recursive tracking
    nodes_to_process = [{"id": str(extension_id), "name": extension_name, "ext": extension_number, "type": extension_type, "path": "Primary Flow"}]
    processed = set()

    def add_case(category, scenario, step, expected, prefix=""):
        nonlocal case_counter
        uat_cases.append({"test_id": f"UAT-{case_counter:04d}", "category": f"{prefix}{category}", "scenario": scenario, "action": step, "expected": expected})
        case_counter += 1

    while nodes_to_process:
        current = nodes_to_process.pop(0)
        c_id, c_name, c_ext, c_type, path_ctx = current['id'], current['name'], current['ext'], current['type'], current['path']
        
        if c_id in processed: continue
        processed.add(c_id)

        cat_prefix = f"[{c_name}] "
        step_prefix = f"[Path: {path_ctx}] " if path_ctx != "Primary Flow" else ""

        # 1. CONNECTIVITY
        if path_ctx == "Primary Flow":
            dids = get_direct_numbers(c_id)
            if dids:
                for did in dids:
                    add_case("Integration", f"External Routing (DID: {did})", f"Dial exact DID: {did} from mobile.", f"Call connects to {c_name} via PSTN.", prefix=cat_prefix)
            else:
                add_case("Integration", "Internal Routing", f"Dial extension {c_ext} internally.", f"Call connects to {c_name}.", prefix=cat_prefix)

        # 2. FETCH ANSWERING RULES (Holds custom rules, after hours, and queue limits)
        ar_res = rc_api_call(f'/restapi/v1.0/account/~/extension/{c_id}/answering-rule', method='GET', raise_error=False)
        rules = ar_res.get('records', []) if ar_res else []

        for rule in rules:
            if not rule.get('enabled', False): continue
            r_type = rule.get('type')

            # --- CUSTOM RULES ---
            if r_type == 'Custom':
                rule_name = rule.get('name', 'Custom Rule')
                conditions = parse_custom_conditions(rule)
                target_name, target_id = resolve_rule_target(rule, ext_map)
                
                add_case("Routing", f"Custom Rule: {rule_name}", f"{step_prefix}Initiate call where: {conditions}.", f"Rule triggers. Routes to: {target_name}.", prefix=cat_prefix)
                if target_id and ext_map.get(target_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": target_id, "name": ext_map[target_id]['name'], "ext": ext_map[target_id]['ext'], "type": ext_map[target_id]['type'], "path": f"Custom Rule '{rule_name}' from {c_name}"})

            # --- AFTER HOURS ---
            elif r_type == 'AfterHours':
                target_name, target_id = resolve_rule_target(rule, ext_map)
                add_case("Routing", "After Hours Routing", f"{step_prefix}Initiate call outside Business Hours.", f"Executes After Hours logic. Routes to: {target_name}.", prefix=cat_prefix)
                if target_id and ext_map.get(target_id, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": target_id, "name": ext_map[target_id]['name'], "ext": ext_map[target_id]['ext'], "type": ext_map[target_id]['type'], "path": f"After Hours from {c_name}"})

            # --- QUEUE DATA (Stored inside BusinessHours) ---
            elif r_type == 'BusinessHours' and c_type == 'Department':
                add_case("Routing", "Business Hours Routing", f"{step_prefix}Initiate call during Business Hours.", f"Call enters queue {c_name}.", prefix=cat_prefix)
                
                # Check explicit greetings
                if any(g.get('type') == 'Introductory' for g in rule.get('greetings', [])):
                    add_case("Queue Logic", "Introductory Greeting", f"Place call into {c_name}.", "Configured Intro Greeting plays fully before ringing.", prefix=cat_prefix)

                queue_settings = rule.get('queue', {})
                
                # Check Max Wait Time
                hold_time = queue_settings.get('holdTime')
                if hold_time:
                    action = queue_settings.get('holdTimeExpirationAction')
                    target_name, target_id = resolve_queue_overflow(action, rule, ext_map)
                    add_case("Queue Boundaries", f"Max Wait Time ({hold_time}s)", f"{step_prefix}Remain on hold in {c_name} for {hold_time} seconds.", f"Timer expires. Call overflows to: {target_name}.", prefix=cat_prefix)
                    if target_id and ext_map.get(target_id, {}).get('type') in ['Department', 'IvrMenu']:
                        nodes_to_process.append({"id": target_id, "name": ext_map[target_id]['name'], "ext": ext_map[target_id]['ext'], "type": ext_map[target_id]['type'], "path": f"Wait Time Overflow from {c_name}"})

                # Check Max Callers
                max_callers = queue_settings.get('maxCallers')
                if max_callers:
                    action = queue_settings.get('maxCallersAction')
                    target_name, target_id = resolve_queue_overflow(action, rule, ext_map)
                    add_case("Queue Boundaries", f"Max Callers Limit ({max_callers})", f"{step_prefix}Simultaneously flood {c_name} with {max_callers + 1} calls.", f"Call {max_callers + 1} instantly overflows to: {target_name}.", prefix=cat_prefix)
                    if target_id and ext_map.get(target_id, {}).get('type') in ['Department', 'IvrMenu']:
                        nodes_to_process.append({"id": target_id, "name": ext_map[target_id]['name'], "ext": ext_map[target_id]['ext'], "type": ext_map[target_id]['type'], "path": f"Max Callers Overflow from {c_name}"})

                # Zero-Out Option (Always maps to Voicemail Recipient in RC)
                zero_name, _ = resolve_queue_overflow('Voicemail', rule, ext_map)
                add_case("Queue Boundaries", "Zero-Out (Press 0)", f"{step_prefix}While listening to hold music, press '0'.", f"Call escapes queue and routes to: {zero_name}.", prefix=cat_prefix)

        # 3. IVR MENU KEYS
        if c_type == 'IvrMenu':
            ivr_info = rc_api_call(f'/restapi/v1.0/ivr-menus/{c_id}', method='GET', raise_error=False) or {}
            for act in ivr_info.get('actions', []):
                key = act.get('input', '')
                if not key: continue 
                
                # Resolve IVR target
                tid = str(act.get('extension', {}).get('id', ''))
                target_name = ext_map.get(tid, {}).get('name', 'Unknown') if tid else "Configured Action"
                
                add_case("IVR Logic", f"Key Mapping: '{key}'", f"{step_prefix}Listen to prompt and press '{key}'.", f"Call routes to: {target_name}.", prefix=cat_prefix)
                if tid and ext_map.get(tid, {}).get('type') in ['Department', 'IvrMenu']:
                    nodes_to_process.append({"id": tid, "name": ext_map[tid]['name'], "ext": ext_map[tid]['ext'], "type": ext_map[tid]['type'], "path": f"Key '{key}' from {c_name}"})

    return uat_cases
