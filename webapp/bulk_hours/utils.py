# webapp/bulk_hours/utils.py
from webapp.rc_api import rc_api_call
import json

# Caches for extension lookups to prevent rate-limiting during bulk fetches & uploads
EXT_CACHE = {}
UPLOAD_ID_CACHE = {}

def _get_extension_display(ext_id):
    """Fetches the extension number and name from RingCentral."""
    if not ext_id or str(ext_id).strip() == '' or ext_id == 'N/A':
        return '', 'Unknown'
    
    ext_id_str = str(ext_id)
    if ext_id_str in EXT_CACHE:
        return EXT_CACHE[ext_id_str]
        
    try:
        res = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id_str}")
        if res:
            ext_num = res.get('extensionNumber', '')
            ext_name = res.get('name', 'Unknown')
            EXT_CACHE[ext_id_str] = (ext_num, ext_name)
            return ext_num, ext_name
    except Exception as e:
        print(f"WARN: Failed to lookup extension {ext_id_str}: {e}")
        pass
        
    EXT_CACHE[ext_id_str] = ('', 'Unknown')
    return '', 'Unknown'

def _lookup_ext_id(ext_number):
    """Looks up the internal RingCentral Extension ID using the Extension Number."""
    if not ext_number or ext_number == 'N/A' or str(ext_number).lower() == 'unknown': 
        return None
        
    ext_str = str(ext_number).strip()
    if ext_str in UPLOAD_ID_CACHE: 
        return UPLOAD_ID_CACHE[ext_str]
        
    try:
        res = rc_api_call(f"/restapi/v1.0/account/~/extension?extensionNumber={ext_str}")
        if res and res.get('records') and len(res['records']) > 0:
            ext_id = str(res['records'][0]['id'])
            UPLOAD_ID_CACHE[ext_str] = ext_id
            return ext_id
    except Exception as e: 
        print(f"WARN: Failed to resolve ID for Ext Number {ext_str}: {e}")
        pass
        
    return None

# ===============================================================
# BUSINESS HOURS FUNCTIONS
# ===============================================================

def fetch_operating_hours(entity_type):
    """Fetches and processes operating hours for a given entity type ('Site' or 'Queue')."""
    try:
        list_endpoint = "/restapi/v1.0/account/~/sites" if entity_type == "Site" else "/restapi/v1.0/account/~/call-queues"
        entities_response = rc_api_call(list_endpoint)
        if not entities_response or 'records' not in entities_response:
            return []

        all_data = []
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for entity in entities_response['records']:
            entity_id = entity.get('id')
            entity_name = entity.get('name', f'Unknown {entity_type}')
            
            hours_endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/business-hours"
            if entity_id == "main-site":
                hours_endpoint = "/restapi/v1.0/account/~/business-hours"

            hours_response = rc_api_call(hours_endpoint)
            entity_row = {"EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name}

            if not hours_response or 'schedule' not in hours_response:
                print(f"WARN: Could not retrieve hours for {entity_name} (ID: {entity_id})")
                for day in days: entity_row[day] = "ERROR"
            else:
                schedule = hours_response.get('schedule', {})
                weekly_ranges = schedule.get('weeklyRanges', {})
                if not weekly_ranges:
                    for day in days: entity_row[day] = "00:00-23:59"
                else:
                    for day in days:
                        day_schedule = weekly_ranges.get(day.lower())
                        if day_schedule:
                            entity_row[day] = f"{day_schedule[0].get('from', 'N/A')}-{day_schedule[0].get('to', 'N/A')}"
                        else:
                            entity_row[day] = "Closed"
            all_data.append(entity_row)
        return all_data
    except Exception as e:
        print(f"FATAL ERROR in fetch_operating_hours: {e}")
        raise e

def update_hours_from_records(records):
    """Processes a list of records and updates RC business hours."""
    results = []
    for record in records:
        entity_id = record.get("EntityID")
        entity_name = record.get("EntityName")
        if not entity_id or not entity_name: continue

        try:
            schedule_from_row = {day.lower(): record.get(day) for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
            api_body = _build_hours_api_body(schedule_from_row)
            
            endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/business-hours"
            if entity_id == "main-site": endpoint = "/restapi/v1.0/account/~/business-hours"

            response = rc_api_call(endpoint, method="PUT", body=api_body)
            if response:
                results.append({"name": entity_name, "status": "success", "message": "Updated successfully."})
            else:
                results.append({"name": entity_name, "status": "error", "message": "API call failed. Check server logs."})
        except Exception as e:
            print(f"ERROR processing update for {entity_name}: {e}")
            results.append({"name": entity_name, "status": "error", "message": str(e)})
    return results

def _build_hours_api_body(schedule):
    """Helper to construct the business hours API body."""
    weekly_ranges = {}
    for day, hours in schedule.items():
        if not hours or str(hours).strip().lower() in ['closed', '']: continue
        if "00:00-23:59" in str(hours) or "00:00-00:00" in str(hours):
            weekly_ranges[day] = [{"from": "00:00", "to": "23:59"}]
            continue
        if '-' in str(hours):
            parts = str(hours).split('-')
            if len(parts) == 2:
                weekly_ranges[day] = [{"from": parts[0].strip(), "to": parts[1].strip()}]
    return {"schedule": {"weeklyRanges": weekly_ranges}}

# ===============================================================
# RULES FUNCTIONS (Base & Custom)
# ===============================================================

def fetch_rules(entity_type, category='all'):
    """Fetches base rules and/or custom rules depending on the category parameter."""
    try:
        list_endpoint = "/restapi/v1.0/account/~/sites" if entity_type == "Site" else "/restapi/v1.0/account/~/call-queues"
        entities_response = rc_api_call(list_endpoint)
        if not entities_response or 'records' not in entities_response:
            return []

        all_rules_data = []
        for entity in entities_response['records']:
            entity_id = entity.get('id')
            entity_name = entity.get('name')
            
            rules_endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/answering-rule"
            if entity_id == "main-site": rules_endpoint = "/restapi/v1.0/account/~/answering-rule"

            rules_summary_response = rc_api_call(rules_endpoint)
            if not rules_summary_response or 'records' not in rules_summary_response:
                continue
                
            all_ext_rules = rules_summary_response['records']

            # 1. Process Default Rules
            if category in ['all', 'default']:
                default_rules = [r for r in all_ext_rules if r.get('type') in ['BusinessHours', 'AfterHours'] or r.get('id') in ['business-hours', 'after-hours']]
                for rule_summary in default_rules:
                    rule_id = rule_summary.get('id')
                    detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_id}")
                    
                    if detailed_rule and 'errorCode' not in detailed_rule:
                        parsed = _parse_rule_details(detailed_rule)
                        all_rules_data.append({
                            "RuleCategory": "Default", "Action": "MODIFY", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": detailed_rule.get('name', rule_id.replace('-', ' ').title()), 
                            "Enabled": detailed_rule.get('enabled', 'N/A'),
                            "ScheduleType": parsed['schedule_type'], "ScheduleDetails": parsed['schedule_details'],
                            "CallAction": parsed['call_action'], "ActionTarget": parsed['action_target'], "ActionTargetName": parsed['action_target_name']
                        })
                    else:
                        all_rules_data.append({
                            "RuleCategory": "Default", "Action": "INFO", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": rule_summary.get('name', rule_id), "Enabled": "Error",
                            "ScheduleType": "N/A", "ScheduleDetails": "N/A", "CallAction": "API Error", "ActionTarget": "N/A", "ActionTargetName": "N/A"
                        })

            # 2. Process Custom Rules
            if category in ['all', 'custom']:
                custom_rules = [r for r in all_ext_rules if r.get('type') == 'Custom']
                if not custom_rules:
                    all_rules_data.append({
                        "RuleCategory": "Custom", "Action": "INFO", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                        "RuleID": "N/A", "RuleName": "No Custom Rules Active", "Enabled": "N/A",
                        "ScheduleType": "N/A", "ScheduleDetails": "Following standard business hours",
                        "CallAction": "N/A", "ActionTarget": "N/A", "ActionTargetName": "N/A"
                    })
                else:
                    for rule_summary in custom_rules:
                        rule_id = rule_summary.get('id')
                        detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_id}")
                        if not detailed_rule or 'errorCode' in detailed_rule: continue
                        
                        parsed = _parse_rule_details(detailed_rule)
                        all_rules_data.append({
                            "RuleCategory": "Custom", "Action": "MODIFY", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": detailed_rule.get('name'), "Enabled": detailed_rule.get('enabled'),
                            "ScheduleType": parsed['schedule_type'], "ScheduleDetails": parsed['schedule_details'],
                            "CallAction": parsed['call_action'], "ActionTarget": parsed['action_target'], "ActionTargetName": parsed['action_target_name']
                        })
        return all_rules_data
    except Exception as e:
        print(f"FATAL ERROR in fetch_rules: {e}")
        raise e

def update_rules_from_records(records):
    """Updates base routing rules or creates/updates custom rules from records."""
    results = []
    for rule in records:
        action = rule.get("Action", "").upper()
        entity_id = rule.get("EntityID")
        entity_name = rule.get("EntityName")
        rule_name = rule.get("RuleName")
        rule_id = rule.get("RuleID")

        if action not in ["NEW", "MODIFY"] or not entity_id:
            continue

        try:
            api_body = _build_rule_api_body(rule)
            if not api_body:
                raise ValueError("Could not construct valid API body from rule data.")

            if rule_id in ['business-hours', 'after-hours']:
                endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/answering-rule/{rule_id}"
                api_body.pop("type", None)
                api_body.pop("name", None)
                response = rc_api_call(endpoint, method="PUT", body=api_body)
                
            elif action == "MODIFY":
                if not rule_id or rule_id == 'N/A': raise ValueError("RuleID is required for MODIFY action.")
                endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/answering-rule/{rule_id}"
                response = rc_api_call(endpoint, method="PUT", body=api_body)
                
            elif action == "NEW":
                endpoint = f"/restapi/v1.0/account/~/extension/{entity_id}/answering-rule"
                response = rc_api_call(endpoint, method="POST", body=api_body)
            
            if response:
                results.append({"name": f"{entity_name} - {rule_name}", "status": "success", "message": f"Action '{action}' successful."})
            else:
                 results.append({"name": f"{entity_name} - {rule_name}", "status": "error", "message": "API call failed. Check server logs."})
        except Exception as e:
            print(f"ERROR processing rule update for {entity_name}: {e}")
            results.append({"name": f"{entity_name} - {rule_name}", "status": "error", "message": str(e)})

    return results

def _parse_rule_details(rule):
    """Parses a detailed rule object into simple, readable strings. Maps ActionTarget dynamically."""
    parsed = {"schedule_type": "Unknown", "schedule_details": "N/A", "call_action": "N/A", "action_target": "N/A", "action_target_name": "N/A"}
    
    schedule = rule.get('schedule', {})
    if not schedule:
        parsed['schedule_type'] = "Always"
    elif 'weeklyRanges' in schedule:
        parsed['schedule_type'] = "Weekly"
        parsed['schedule_details'] = "Custom schedule"
    elif 'ranges' in schedule and schedule.get('ranges'):
        parsed['schedule_type'] = "DateRange"
        r = schedule['ranges'][0]
        parsed['schedule_details'] = f"{r.get('from')} to {r.get('to')}"
    
    call_action = rule.get('callHandlingAction')
    if not call_action:
        parsed['call_action'] = "Not Specified"
        return parsed
        
    parsed['call_action'] = call_action
    
    if call_action == 'ForwardCalls' and 'forwarding' in rule:
        fwd_nums = rule['forwarding'].get('rules', [{}])[0].get('forwardingNumbers', [])
        if fwd_nums:
            target = fwd_nums[0]
            parsed['action_target'] = target.get('phoneNumber', 'Unknown')
            parsed['action_target_name'] = target.get('label', 'External Number')
            
    elif call_action == 'TransferToExtension':
        ext_info = rule.get('transfer', {}).get('extension', {})
        target_id = ext_info.get('id', 'N/A')
        target_num = ext_info.get('extensionNumber')
        target_name = ext_info.get('name')
        
        # If API omits the extension number/name (very common with IVRs), fetch it dynamically
        if (not target_num or not target_name) and target_id != 'N/A':
            fetched_num, fetched_name = _get_extension_display(target_id)
            target_num = target_num or fetched_num
            target_name = target_name or fetched_name
            
        parsed['action_target'] = target_num or 'Unknown'
        parsed['action_target_name'] = target_name or 'Unknown'
        
    elif call_action == 'UnconditionalForwarding':
        parsed['action_target'] = rule.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown')
        parsed['action_target_name'] = 'External Number'
        
    elif call_action == 'TakeMessagesOnly':
        parsed['call_action'] = 'Voicemail'
        voicemail_ext = rule.get('voicemail', {}).get('recipient', {})
        if voicemail_ext:
            target_id = voicemail_ext.get('id', 'N/A')
            target_num = voicemail_ext.get('extensionNumber')
            target_name = voicemail_ext.get('name')
            
            if (not target_num or not target_name) and target_id != 'N/A':
                fetched_num, fetched_name = _get_extension_display(target_id)
                target_num = target_num or fetched_num
                target_name = target_name or fetched_name
                
            parsed['action_target'] = target_num or 'Self'
            parsed['action_target_name'] = target_name or 'Voicemail Box'
        else:
            parsed['action_target'] = 'Self'
            parsed['action_target_name'] = 'Voicemail Box'

    elif call_action == 'AgentQueue':
        parsed['action_target'] = 'Queue Members'
        parsed['action_target_name'] = 'Agent Queue'

    elif call_action == 'PlayAnnouncementOnly':
        parsed['action_target'] = 'Announcement'
        parsed['action_target_name'] = 'Announcement'
    
    return parsed

def _build_rule_api_body(rule_data):
    """Constructs the API request body, handling internal ID lookups using the CSV's Ext Number."""
    body = {
        "enabled": str(rule_data.get("Enabled", "true")).lower() == 'true',
        "type": "Custom",
        "name": rule_data.get("RuleName")
    }

    schedule_type = rule_data.get("ScheduleType")
    if schedule_type == "DateRange":
        details = rule_data.get("ScheduleDetails", "").split(" to ")
        if len(details) == 2:
            body["schedule"] = {"ranges": [{"from": details[0].strip(), "to": details[1].strip()}]}

    call_action = rule_data.get("CallAction", "")
    action_target = str(rule_data.get("ActionTarget", "")).strip()
    
    body["callHandlingAction"] = call_action

    if call_action == "TransferToExtension":
        target_id = _lookup_ext_id(action_target)
        if target_id:
            body["transfer"] = {"extension": {"id": target_id}}
        else:
            raise ValueError(f"Target Extension Number '{action_target}' could not be resolved.")

    elif call_action in ["ForwardCalls", "UnconditionalForwarding"]:
        # Unconditional Forwarding handles standard E.164 phone numbers seamlessly
        if action_target and action_target != "N/A":
            body["callHandlingAction"] = "UnconditionalForwarding"
            body["unconditionalForwarding"] = {"phoneNumber": action_target}
            
    elif call_action in ["Voicemail", "TakeMessagesOnly"]:
        body["callHandlingAction"] = "TakeMessagesOnly"
        if action_target and action_target.lower() != 'self':
            target_id = _lookup_ext_id(action_target)
            if target_id:
                body["voicemail"] = {"recipient": {"id": target_id}}
            else:
                raise ValueError(f"Target Voicemail Extension '{action_target}' could not be resolved.")
    
    elif call_action == "AgentQueue":
        body["callHandlingAction"] = "AgentQueue"
        
    elif call_action == "PlayAnnouncementOnly":
        body["callHandlingAction"] = "PlayAnnouncementOnly"
    
    return body
