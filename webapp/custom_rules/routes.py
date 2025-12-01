import io
import pandas as pd
import requests
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
# Import the new transformer function
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
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    
    for index, row in df.iterrows():
        raw_ext_num = row.get('Ext Number')
        if pd.isna(raw_ext_num): 
            continue

        try:
            # 1. Resolve Extension ID
            ext_id = get_extension_id(raw_ext_num)
            if not ext_id:
                results.append(f"Row {index}: ⚠️ Extension {raw_ext_num} not found.")
                continue

            # 2. Build Payload (V1 format initially)
            payload, action_type = build_rule_payload(row, ext_id)

            # 3. Handle Complex Actions (Add details to V1 Payload)
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

            # 4. Prepare API Paths
            rule_id = row.get('Rule ID')
            is_update = pd.notna(rule_id) and str(rule_id).strip()
            
            # V1 Paths
            v1_base = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
            v1_url = f"{v1_base}/{str(rule_id).replace('.0', '').strip()}" if is_update else v1_base
            
            # V2 Paths
            v2_base = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
            v2_url = f"{v2_base}/{str(rule_id).replace('.0', '').strip()}" if is_update else v2_base

            method = "PUT" if is_update else "POST"

            try:
                # Attempt V1 API first
                rc_api_call(v1_url, method=method, json=payload, raise_error=True)
                results.append(f"✅ {method} Rule Ext {raw_ext_num} (V1)")
            
            except requests.exceptions.HTTPError as http_err:
                # Check for "NewCallHandlingAndForwarding" feature error
                error_text = http_err.response.text
                if "NewCallHandlingAndForwarding" in error_text:
                    # Retry with V2 API using the TRANSFORMED payload
                    try:
                        v2_payload = transform_v1_to_v2(payload) # <--- CONVERT HERE
                        rc_api_call(v2_url, method=method, json=v2_payload, raise_error=True)
                        results.append(f"✅ {method} Rule Ext {raw_ext_num} (V2)")
                    except Exception as v2_err:
                         # Detailed error logging for V2 failures
                         err_msg = str(v2_err)
                         if hasattr(v2_err, 'response') and v2_err.response is not None:
                             err_msg = f"{v2_err.response.status_code} {v2_err.response.text}"
                         results.append(f"❌ Failed V2 Retry Ext {raw_ext_num}: {err_msg}")
                else:
                    raise http_err

        except requests.exceptions.HTTPError as http_err:
            error_msg = "Unknown API Error"
            try:
                error_data = http_err.response.json()
                if 'message' in error_data:
                    error_msg = error_data['message']
                if 'errors' in error_data and len(error_data['errors']) > 0:
                    error_msg += f" ({error_data['errors'][0].get('message', '')})"
            except:
                error_msg = http_err.response.text
            
            results.append(f"❌ Failed Ext {raw_ext_num}: {error_msg}")

        except Exception as e:
            results.append(f"❌ System Error Ext {raw_ext_num}: {str(e)}")

    return jsonify({"logs": results})

@custom_rules_bp.route('/api/custom_rules/template', methods=['GET'])
def download_template():
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 
        'Caller ID', 'Called Number', 'Work or After Hours', 
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 
        'Specific Dates', 'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient'
    ]
    example_data = [{
        'Ext Number': '101', 'Ext Name': 'John Doe', 'Rule Name': 'Holiday Rule',
        'Rule ID': '', 'Enabled': 'Yes', 'Caller ID': '1234567890',
        'Called Number': '', 'Monday': '9:00 AM - 5:00 PM',
        'Action': 'Transfer to External', 'External Number': '15550001234'
    }]
    
    df = pd.DataFrame(example_data, columns=columns)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template')
        worksheet = writer.sheets['Template']
        for column in worksheet.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            worksheet.column_dimensions[column[0].column_letter].width = length + 5

    output.seek(0)
    
    return send_file(
        output, 
        download_name="custom_rules_template.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
