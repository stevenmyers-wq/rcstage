import io
import json
import pandas as pd
import requests
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from .utils import build_v1_payload, format_phone

custom_rules_bp = Blueprint('custom_rules', __name__)

# --- HELPERS ---

def get_extension_id(extension_number):
    """Resolves Extension Number to ID."""
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'): ext_num = ext_num[:-2]

    resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'extensionNumber': ext_num})
    if resp and 'records' in resp and len(resp['records']) > 0:
        return resp['records'][0]['id']
    return None

def transform_v1_to_v2(v1_payload, owner_ext_id):
    """
    Reconstructs V1 data into V2 Interaction Rule format.
    FIX: Only adds 'prompt' to VoiceMail/Announcement targets. 
    Removes it from Phone/Extension targets.
    """
    v2 = {
        "displayName": v1_payload.get("name"), 
        "enabled": v1_payload.get("enabled"),
        "conditions": [],
        "dispatching": {
            "type": "Terminate",
            "actions": []
        }
    }
    
    # --- 1. CONDITIONS ---
    interaction_cond = {
        "type": "Interaction",
        "to": [],
        "from": []
    }

    if "calledNumbers" in v1_payload:
        interaction_cond["to"] = [item['phoneNumber'] for item in v1_payload['calledNumbers']]

    if "callers" in v1_payload:
        interaction_cond["from"] = [item['callerId'] for item in v1_payload['callers']]

    v2["conditions"].append(interaction_cond)

    # --- 2. ACTIONS ---
    v1_act = v1_payload.get("callHandlingAction")
    
    # Define Prompt for Voicemail Targets Only
    vm_prompt = {
        "greeting": {
            "effectiveGreetingType": "Default" 
        }
    }

    # Fallback VM Target (Required by V2 for safety on forwarding rules)
    fallback_vm_target = {
        "type": "VoiceMailTerminatingTarget",
        "mailbox": {"id": owner_ext_id},
        "dispatchingType": "Ringing",
        "prompt": vm_prompt # Mandatory for VM
    }

    # CASE A: Unconditional Forwarding
    if v1_act == "UnconditionalForwarding":
        dest_num = v1_payload.get("unconditionalForwarding", {}).get("phoneNumber")
        formatted_dest = format_phone(dest_num)
        
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "PhoneNumberTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                {
                    "type": "PhoneNumberTerminatingTarget",
                    "destination": {"phoneNumber": formatted_dest},
                    "dispatchingType": "Terminating" 
                    # NO PROMPT HERE
                },
                fallback_vm_target # HAS PROMPT
            ]
        }
        v2["dispatching"]["actions"].append(action)

    # CASE B: Transfer to Extension
    elif v1_act == "TransferToExtension":
        target_ext_id = v1_payload.get("transfer", {}).get("extension", {}).get("id")
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "ExtensionTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                {
                    "type": "ExtensionTerminatingTarget",
                    "extension": {"id": target_ext_id},
                    "dispatchingType": "Terminating"
                    # NO PROMPT HERE
                },
                fallback_vm_target # HAS PROMPT
            ]
        }
        v2["dispatching"]["actions"].append(action)

    # CASE C: Voicemail
    elif v1_act == "TakeMessagesOnly":
        vm_recipient_id = v1_payload.get("voicemail", {}).get("recipient", {}).get("id")
        action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "VoiceMailTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                {
                    "type": "VoiceMailTerminatingTarget",
                    "mailbox": {"id": vm_recipient_id},
                    "dispatchingType": "Terminating",
                    "prompt": vm_prompt # Mandatory
                }
            ]
        }
        v2["dispatching"]["actions"].append(action)
        
    # CASE D: Play Announcement
    elif v1_act == "PlayAnnouncementOnly":
         action = {
            "type": "TerminatingAction",
            "terminatingTargetType": "PlayAnnouncementTerminatingTarget",
            "ringingTargetType": "VoiceMailTerminatingTarget",
            "targets": [
                {
                     "type": "PlayAnnouncementTerminatingTarget",
                     "dispatchingType": "Terminating",
                     "prompt": vm_prompt # Mandatory
                },
                fallback_vm_target # HAS PROMPT
            ]
         }
         v2["dispatching"]["actions"].append(action)

    return v2

# --- ROUTES ---

@custom_rules_bp.route('/api/update_rules', methods=['POST'])
@require_rc_token
def update_rules():
    if 'file' not in request.files: return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    
    try:
        if file.filename.endswith('.csv'): df = pd.read_csv(file)
        else: df = pd.read_excel(file)
        df.columns = df.columns.str.strip()
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    
    for index, row in df.iterrows():
        raw_ext_num = row.get('Ext Number')
        if pd.isna(raw_ext_num): continue

        try:
            ext_id = get_extension_id(raw_ext_num)
            if not ext_id:
                results.append(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.")
                continue

            # Build V1 Payload
            payload, action_type = build_v1_payload(row, ext_id)

            # Pre-flight Check
            if not any(k in payload for k in ['callers', 'calledNumbers', 'schedule']):
                results.append(f"⚠️ Ext {raw_ext_num}: Skipped - No conditions found.")
                continue

            # Add Complex Action Details
            if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
                raw_ph = str(row.get('External Number')).strip()
                payload['unconditionalForwarding'] = {'phoneNumber': format_phone(raw_ph)}
            elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
                target_id = get_extension_id(row.get('Transfer Extension'))
                if target_id: payload['transfer'] = {'extension': {'id': target_id}}
                else:
                    results.append(f"⚠️ Target Ext {row.get('Transfer Extension')} not found.")
                    continue
            elif action_type == 'TakeMessagesOnly' and pd.notna(row.get('Voicemail Recipient')):
                vm_id = get_extension_id(row.get('Voicemail Recipient'))
                if vm_id: payload['voicemail'] = {'recipient': {'id': vm_id}}
                else: payload['voicemail'] = {'recipient': {'id': ext_id}}

            # Paths
            rule_id = str(row.get('Rule ID')).replace('.0', '').strip() if pd.notna(row.get('Rule ID')) else ""
            is_update = bool(rule_id)
            
            v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
            if is_update: v1_url += f"/{rule_id}"
            
            v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
            if is_update: v2_url += f"/{rule_id}"

            method = "PUT" if is_update else "POST"

            # --- EXECUTE ---
            try:
                # Try V1
                rc_api_call(v1_url, method=method, json=payload, raise_error=True)
                results.append(f"✅ {method} Rule Ext {raw_ext_num} (V1)")
            
            except requests.exceptions.HTTPError as http_err:
                if "NewCallHandlingAndForwarding" in http_err.response.text:
                    try:
                        # BUILD V2 PAYLOAD with EXT ID
                        v2_payload = transform_v1_to_v2(payload, ext_id)
                        
                        if not v2_payload['conditions']:
                             results.append(f"⚠️ Ext {raw_ext_num}: V2 Skipped - Conditions empty.")
                             continue

                        rc_api_call(v2_url, method=method, json=v2_payload, raise_error=True)
                        results.append(f"✅ {method} Rule Ext {raw_ext_num} (V2)")
                        
                    except requests.exceptions.HTTPError as v2_err:
                        debug_json = json.dumps(v2_payload, default=str)
                        results.append(f"❌ V2 Error Ext {raw_ext_num}: {v2_err.response.text}\nSent: {debug_json}")
                    except Exception as ex:
                        results.append(f"❌ V2 Logic Error: {str(ex)}")
                else:
                    raise http_err

        except requests.exceptions.HTTPError as he:
            try: msg = he.response.json().get('message', he.response.text)
            except: msg = he.response.text
            results.append(f"❌ API Error Ext {raw_ext_num}: {msg}")
        except Exception as e:
            results.append(f"❌ System Error Ext {raw_ext_num}: {str(e)}")

    return jsonify({"logs": results})

@custom_rules_bp.route('/api/custom_rules/template', methods=['GET'])
def download_template():
    columns = ['Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 'Caller ID', 'Called Number', 'Work or After Hours', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Specific Dates', 'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient']
    df = pd.DataFrame([], columns=columns)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template')
        worksheet = writer.sheets['Template']
        for column in worksheet.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            worksheet.column_dimensions[column[0].column_letter].width = length + 5
    output.seek(0)
    return send_file(output, download_name="custom_rules_template.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
