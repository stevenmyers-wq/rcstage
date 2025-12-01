import io
import pandas as pd
import requests
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from .utils import build_rule_payload, transform_v1_to_v2

custom_rules_bp = Blueprint('custom_rules', __name__)

def get_extension_id(extension_number):
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'): 
        ext_num = ext_num[:-2]

    resp = rc_api_call(
        '/restapi/v1.0/account/~/extension', 
        params={'extensionNumber': ext_num}
    )
    
    if resp and 'records' in resp and len(resp['records']) > 0:
        return resp['records'][0]['id']
    return None

@custom_rules_bp.route('/api/update_rules', methods=['POST'])
@require_rc_token
def update_rules():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']
    
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
            
        # --- 1. Sanitize Headers (Fixes "Called Number " spaces) ---
        df.columns = df.columns.str.strip()
        
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    
    for index, row in df.iterrows():
        raw_ext_num = row.get('Ext Number')
        if pd.isna(raw_ext_num): 
            continue

        try:
            # 2. Resolve Extension ID
            ext_id = get_extension_id(raw_ext_num)
            if not ext_id:
                results.append(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.")
                continue

            # 3. Build Payload
            payload, action_type = build_rule_payload(row, ext_id)

            # --- Pre-Flight Check ---
            # If payload has no conditions (no callers, calledNumbers, or schedule), API will fail.
            has_conditions = any(k in payload for k in ['callers', 'calledNumbers', 'schedule'])
            if not has_conditions:
                results.append(f"⚠️ Row {index}: Skipped - No conditions found (Check 'Called Number' or 'Caller ID' columns)")
                continue

            # 4. Handle Complex Actions
            if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
                payload['unconditionalForwarding'] = {'phoneNumber': str(row.get('External Number')).strip()}
            
            elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
                target_id = get_extension_id(row.get('Transfer Extension'))
                if target_id: 
                    payload['transfer'] = {'extension': {'id': target_id}}
                else:
                    results.append(f"⚠️ Target Ext {row.get('Transfer Extension')} not found.")
                    continue
            
            elif action_type == 'TakeMessagesOnly' and pd.notna(row.get('Voicemail Recipient')):
                vm_id = get_extension_id(row.get('Voicemail Recipient'))
                if vm_id: 
                    payload['voicemail'] = {'recipient': {'id': vm_id}}

            # 5. API Paths
            rule_id = row.get('Rule ID')
            is_update = pd.notna(rule_id) and str(rule_id).strip()
            rule_id_str = str(rule_id).replace('.0', '').strip() if is_update else ""
            
            # V1 Path
            v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
            if is_update: v1_url += f"/{rule_id_str}"
            
            # V2 Path
            v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
            if is_update: v2_url += f"/{rule_id_str}"

            method = "PUT" if is_update else "POST"

            try:
                # Attempt V1 API
                rc_api_call(v1_url, method=method, json=payload, raise_error=True)
                results.append(f"✅ {method} Rule Ext {raw_ext_num} (V1)")
            
            except requests.exceptions.HTTPError as http_err:
                error_text = http_err.response.text
                # Check for V2 Requirement
                if "NewCallHandlingAndForwarding" in error_text:
                    try:
                        # Transform to V2
                        v2_payload = transform_v1_to_v2(payload)
                        
                        # Verify V2 Conditions before sending
                        if not v2_payload['conditions']:
                             results.append(f"⚠️ Ext {raw_ext_num}: Conditions empty after V2 transform.")
                             continue
                             
                        rc_api_call(v2_url, method=method, json=v2_payload, raise_error=True)
                        results.append(f"✅ {method} Rule Ext {raw_ext_num} (V2)")
                    except Exception as v2_err:
                         err_msg = str(v2_err)
                         if hasattr(v2_err, 'response') and v2_err.response is not None:
                             err_msg = f"{v2_err.response.status_code} {v2_err.response.text}"
                         results.append(f"❌ Failed V2 Retry Ext {raw_ext_num}: {err_msg}")
                else:
                    raise http_err

        except requests.exceptions.HTTPError as http_err:
            error_msg = http_err.response.text
            try:
                err_json = http_err.response.json()
                if 'message' in err_json: error_msg = err_json['message']
                if 'errors' in err_json and err_json['errors']:
                    error_msg += f" ({err_json['errors'][0].get('message')})"
            except: pass
            results.append(f"❌ Failed Ext {raw_ext_num}: {error_msg}")

        except Exception as e:
            results.append(f"❌ System Error Ext {raw_ext_num}: {str(e)}")

    return jsonify({"logs": results})

@custom_rules_bp.route('/api/custom_rules/template', methods=['GET'])
def download_template():
    # ... (Same as before) ...
    # Ensure this matches what you had previously or just keep your existing download_template
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 
        'Caller ID', 'Called Number', 'Work or After Hours', 
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 
        'Specific Dates', 'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient'
    ]
    # ... rest of function ...
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
