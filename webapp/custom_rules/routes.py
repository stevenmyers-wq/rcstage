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

def get_phone_number_id(phone_number):
    """
    CRITICAL FOR V2: Resolves an E.164 string to a RingCentral ID.
    V2 'CalledNumber' conditions REQUIRE an ID, not a string.
    """
    try:
        resp = rc_api_call('/restapi/v1.0/account/~/phone-number', params={'phoneNumber': phone_number})
        if resp and 'records' in resp and len(resp['records']) > 0:
            return resp['records'][0]['id']
    except:
        pass
    return None

def build_v2_payload(v1_payload):
    """
    Reconstructs V1 data into V2 Interaction Rule format.
    PERFORMS API LOOKUPS for Called Numbers.
    """
    v2 = {
        "name": v1_payload.get("name"),
        "enabled": v1_payload.get("enabled"),
        "conditions": {},
        "actions": []
    }
    
    # 1. Map Callers (Strings are allowed in V2)
    if "callers" in v1_payload:
        v2["conditions"]["callers"] = v1_payload["callers"]
        
    # 2. Map Schedule
    if "schedule" in v1_payload:
        v2["conditions"]["schedule"] = v1_payload["schedule"]
        
    # 3. Map Called Numbers (CRITICAL: Must convert String -> ID)
    if "calledNumbers" in v1_payload:
        v2_called = []
        for item in v1_payload["calledNumbers"]:
            ph_str = item.get("phoneNumber")
            if ph_str:
                ph_id = get_phone_number_id(ph_str) # <--- API LOOKUP
                if ph_id:
                    v2_called.append({"id": ph_id})
                else:
                    print(f"DEBUG: Could not find ID for {ph_str}")
        
        if v2_called:
            v2["conditions"]["calledNumbers"] = v2_called
        else:
            # If we had called numbers but failed to resolve ANY IDs, 
            # return None to signal failure (avoids sending empty condition)
            return None

    # 4. Map Actions (Array Format)
    v1_act = v1_payload.get("callHandlingAction")
    act_obj = {"type": "ForwardCalls"} # Default

    if v1_act == "UnconditionalForwarding":
        act_obj["type"] = "UnconditionalForwarding"
        if "unconditionalForwarding" in v1_payload:
            raw = v1_payload["unconditionalForwarding"].get("phoneNumber")
            act_obj["phoneNumber"] = format_phone(raw)

    elif v1_act == "TransferToExtension":
        act_obj["type"] = "Transfer"
        if "transfer" in v1_payload:
            act_obj["extension"] = v1_payload["transfer"].get("extension")

    elif v1_act == "TakeMessagesOnly":
        act_obj["type"] = "Voicemail"
        if "voicemail" in v1_payload:
            act_obj["extension"] = v1_payload["voicemail"].get("recipient")

    elif v1_act == "PlayAnnouncementOnly":
        act_obj["type"] = "PlayAnnouncement"
        # Note: V2 PlayAnnouncement implies an announcement ID. 
        # If missing, RC might default or error. We send type and hope for default.

    v2["actions"].append(act_obj)
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
            # Resolve Ext ID
            ext_id = get_extension_id(raw_ext_num)
            if not ext_id:
                results.append(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.")
                continue

            # Build Base Payload (V1 style)
            payload, action_type = build_v1_payload(row, ext_id)

            # Pre-flight Check
            if not any(k in payload for k in ['callers', 'calledNumbers', 'schedule']):
                results.append(f"⚠️ Ext {raw_ext_num}: Skipped - No conditions found.")
                continue

            # Add Complex Action Details
            if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
                payload['unconditionalForwarding'] = {'phoneNumber': str(row.get('External Number')).strip()}
            elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
                target_id = get_extension_id(row.get('Transfer Extension'))
                if target_id: payload['transfer'] = {'extension': {'id': target_id}}
                else:
                    results.append(f"⚠️ Target Ext {row.get('Transfer Extension')} not found.")
                    continue
            elif action_type == 'TakeMessagesOnly' and pd.notna(row.get('Voicemail Recipient')):
                vm_id = get_extension_id(row.get('Voicemail Recipient'))
                if vm_id: payload['voicemail'] = {'recipient': {'id': vm_id}}

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
                # Check for V2 Upgrade
                if "NewCallHandlingAndForwarding" in http_err.response.text:
                    try:
                        # BUILD V2 PAYLOAD (With API Lookups)
                        v2_payload = build_v2_payload(payload)
                        
                        # Check validity
                        if v2_payload is None:
                            results.append(f"❌ Ext {raw_ext_num}: V2 Conversion Failed. Could not find ID for Called Number.")
                            continue
                        
                        if not v2_payload['conditions']:
                            results.append(f"⚠️ Ext {raw_ext_num}: V2 Payload empty conditions (IDs might be missing).")
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
    # ... (Keep existing template logic) ...
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
