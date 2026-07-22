from webapp.rc_api import rc_api_call
import json

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

            # 1. Fetch Summary to get Valid Rule IDs (prevents 404s and N/A errors)
            rules_summary_response = rc_api_call(rules_endpoint)
            if not rules_summary_response or 'records' not in rules_summary_response:
                continue
                
            all_ext_rules = rules_summary_response['records']

            # 2. Process Default Rules (In-Hours and Out-of-Hours routing)
            if category in ['all', 'default']:
                default_rules = [r for r in all_ext_rules if r.get('type') in ['BusinessHours', 'AfterHours'] or r.get('id') in ['business-hours', 'after-hours']]
                
                for rule_summary in default_rules:
                    rule_id = rule_summary.get('id')
                    detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_id}")
                    
                    if detailed_rule and 'errorCode' not in detailed_rule:
                        parsed = _parse_rule_details(detailed_rule)
                        all_rules_data.append({
                            "RuleCategory": "Default",
                            "Action": "MODIFY", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": detailed_rule.get('name', rule_id.replace('-', ' ').title()), 
                            "Enabled": detailed_rule.get('enabled', 'N/A'),
                            "ScheduleType": parsed['schedule_type'], "ScheduleDetails": parsed['schedule_details'],
                            "CallAction": parsed['call_action'], "ActionTarget": parsed['action_target'],
                            "ActionTargetID": parsed['action_target_id']
                        })
                    else:
                        all_rules_data.append({
                            "RuleCategory": "Default", "Action": "INFO", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": rule_summary.get('name', rule_id), "Enabled": "Error",
                            "ScheduleType": "N/A", "ScheduleDetails": "N/A", "CallAction": "API Error", "ActionTarget": "N/A", "ActionTargetID": "N/A"
                        })

            # 3. Process Custom Rules
            if category in ['all', 'custom']:
                custom_rules = [r for r in all_ext_rules if r.get('type') == 'Custom']
                
                if not custom_rules:
                    all_rules_data.append({
                        "RuleCategory": "Custom",
                        "Action": "INFO", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                        "RuleID": "N/A", "RuleName": "No Custom Rules Active", "Enabled": "N/A",
                        "ScheduleType": "N/A", "ScheduleDetails": "Following standard business hours",
                        "CallAction": "N/A", "ActionTarget": "N/A", "ActionTargetID": "N/A"
                    })
                else:
                    for rule_summary in custom_rules:
                        rule_id = rule_summary.get('id')
                        detailed_rule = rc_api_call(f"{rules_endpoint}/{rule_id}")
                        if not detailed_rule or 'errorCode' in detailed_rule: continue
                        
                        parsed = _parse_rule_details(detailed_rule)
                        all_rules_data.append({
                            "RuleCategory": "Custom",
                            "Action": "MODIFY", "EntityType": entity_type, "EntityID": entity_id, "EntityName": entity_name,
                            "RuleID": rule_id, "RuleName": detailed_rule.get('name'), "Enabled": detailed_rule.get('enabled'),
                            "ScheduleType": parsed['schedule_type'], "ScheduleDetails": parsed['schedule_details'],
                            "CallAction": parsed['call_action'], "ActionTarget": parsed['action_target'],
                            "ActionTargetID": parsed['action_target_id']
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

            # Base rules (In/Out of Hours) cannot have 'type' or 'name' modified via this endpoint
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
    """Parses a detailed rule object into simple, readable strings including extensions/numbers."""
    parsed = {"schedule_type": "Unknown", "schedule_details": "N/A", "call_action": "N/A", "action_target": "N/A", "action_target_id": "N/A"}
    
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
    
    # Map Action Targets correctly depending on routing method
    if call_action == 'ForwardCalls' and 'forwarding' in rule:
        fwd_nums = rule['forwarding'].get('rules', [{}])[0].get('forwardingNumbers', [])
        if fwd_nums:
            target = fwd_nums[0]
            parsed['action_target'] = target.get('phoneNumber', target.get('label', 'Unknown'))
            parsed['action_target_id'] = target.get('id', 'N/A')
            
    elif call_action == 'TransferToExtension':
        ext_info = rule.get('transfer', {}).get('extension', {})
        parsed['action_target'] = ext_info.get('extensionNumber', 'Unknown')
        parsed['action_target_id'] = ext_info.get('id', 'N/A')
        
    elif call_action == 'UnconditionalForwarding':
        parsed['action_target'] = rule.get('unconditionalForwarding', {}).get('phoneNumber', 'Unknown')
        
    elif call_action == 'TakeMessagesOnly':
        parsed['call_action'] = 'Voicemail'
        voicemail_ext = rule.get('voicemail', {}).get('recipient', {})
        if voicemail_ext:
            parsed['action_target'] = voicemail_ext.get('extensionNumber', 'Self')
            parsed['action_target_id'] = voicemail_ext.get('id', 'N/A')

    elif call_action == 'AgentQueue':
        parsed['action_target'] = 'Queue Members'

    elif call_action == 'PlayAnnouncementOnly':
        parsed['action_target'] = 'Announcement'
    
    return parsed


def _build_rule_api_body(rule_data):
    """Constructs the complex API request body, handling extension ID lookups if users type new numbers."""
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
    action_target_id = str(rule_data.get("ActionTargetID", "")).strip()
    
    body["callHandlingAction"] = call_action

    if call_action == "TransferToExtension":
        target_id = action_target_id
        # Dynamic ID Lookup if user provides an Extension Number but no ID
        if action_target and action_target != "N/A" and (not target_id or target_id == "N/A"):
            res = rc_api_call(f"/restapi/v1.0/account/~/extension?extensionNumber={action_target}")
            if res and res.get('records'):
                target_id = str(res['records'][0]['id'])

        if target_id and target_id != "N/A":
            body["transfer"] = {"extension": {"id": target_id}}

    elif call_action == "ForwardCalls":
        if action_target_id and action_target_id != "N/A":
            body["forwarding"] = {"rules": [{"forwardingNumbers": [{"id": action_target_id}]}]}
        elif action_target and action_target != "N/A":
            # Fallback to Unconditional Forwarding if an external number is provided
            body["callHandlingAction"] = "UnconditionalForwarding"
            body["unconditionalForwarding"] = {"phoneNumber": action_target}
            
    elif call_action == "UnconditionalForwarding":
        if action_target and action_target != "N/A":
            body["unconditionalForwarding"] = {"phoneNumber": action_target}
            
    elif call_action == "Voicemail" or call_action == "TakeMessagesOnly":
        body["callHandlingAction"] = "TakeMessagesOnly"
        target_id = action_target_id
        if action_target and action_target != "N/A" and action_target.lower() != "self" and (not target_id or target_id == "N/A"):
            res = rc_api_call(f"/restapi/v1.0/account/~/extension?extensionNumber={action_target}")
            if res and res.get('records'):
                target_id = str(res['records'][0]['id'])
        if target_id and target_id != "N/A":
            body["voicemail"] = {"recipient": {"id": target_id}}
    
    elif call_action == "AgentQueue":
        body["callHandlingAction"] = "AgentQueue"
        
    elif call_action == "PlayAnnouncementOnly":
        body["callHandlingAction"] = "PlayAnnouncementOnly"
    
    return body
