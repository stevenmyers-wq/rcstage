import io
import json
import pandas as pd
import requests
from datetime import datetime # <--- ADDED MISSING IMPORT
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from .utils import build_v1_payload, format_phone, parse_rule_to_row, transform_v1_to_v2

custom_rules_bp = Blueprint('custom_rules', __name__)

# --- HELPERS ---
def get_extension_id(extension_number):
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'): ext_num = ext_num[:-2]
    resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'extensionNumber': ext_num})
    if resp and 'records' in resp and len(resp['records']) > 0:
        return resp['records'][0]['id']
    return None

def get_user_devices(ext_id):
    try:
        resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/device')
        return resp.get('records', []) if resp else []
    except: return []

# --- AUDIT ROUTE ---
@custom_rules_bp.route('/api/custom_rules/audit', methods=['GET'])
@require_rc_token
def audit_rules():
    """
    Fetches ALL custom rules.
    Prioritizes V2 API check. Falls back to V1 only if V2 fails.
    """
    try:
        # 1. Fetch All User Extensions
        ext_resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000, 'type': 'User'})
        if not ext_resp or 'records' not in ext_resp:
            return jsonify({"error": "Failed to fetch extensions list"}), 500
        
        extensions = ext_resp['records']
        audit_data = []

        for ext in extensions:
            ext_id = ext['id']
            if ext['status'] == 'Disabled': continue

            rules_found = False

            # --- STRATEGY: TRY V2 FIRST (New Architecture) ---
            try:
                v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
                v2_resp = rc_api_call(v2_url, raise_error=True)
                
                if v2_resp and 'records' in v2_resp:
                    for rule in v2_resp['records']:
                        row = parse_rule_to_row(ext, rule, is_v2=True)
                        audit_data.append(row)
                    rules_found = True
            except Exception:
                pass # V2 Failed, proceed to V1 fallback

            # --- STRATEGY: FALLBACK TO V1 (Legacy) ---
            if not rules_found:
                try:
                    v1_resp = rc_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}/answering-rule', params={'view': 'Detailed'})
                    if v1_resp and 'records' in v1_resp:
                        for rule in v1_resp['records']:
                            if rule['type'] == 'Custom':
                                row = parse_rule_to_row(ext, rule, is_v2=False)
                                audit_data.append(row)
                except:
                    pass 

        # 3. Generate Excel
        if not audit_data:
            audit_data = [{'Ext Number': 'No Data', 'Rule Name': 'No Custom Rules Found'}]

        df = pd.DataFrame(audit_data)
        
        # Ensure Column Order
        cols = ['Ext Number', 'Ext Name', 'Rule ID', 'Rule Name', 'Enabled', 'Caller ID', 'Called Number', 
                'Action', 'External Number', 'Transfer Extension', 'Voicemail Recipient']
        
        for c in cols: 
            if c not in df.columns: df[c] = ''
            
        df = df[cols]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Audit')
            worksheet = writer.sheets['Audit']
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = length + 5
        
        output.seek(0)
        return send_file(
            output, 
            download_name=f"Rule_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx", 
            as_attachment=True, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

            user_devices = get_user_devices(ext_id)
            payload, action_type = build_v1_payload(row, ext_id)

            if not any(k in payload for k in ['callers', 'calledNumbers', 'schedule']):
                results.append(f"⚠️ Ext {raw_ext_num}: Skipped - No conditions found.")
                continue

            # Add Actions (Re-using fixed logic)
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

            rule_id = str(row.get('Rule ID')).replace('.0', '').strip() if pd.notna(row.get('Rule ID')) else ""
            is_update = bool(rule_id)
            
            v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule"
            if is_update: v1_url += f"/{rule_id}"
            v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
            if is_update: v2_url += f"/{rule_id}"

            method = "PUT" if is_update else "POST"

            try:
                rc_api_call(v1_url, method=method, json=payload, raise_error=True)
                results.append(f"✅ {method} Rule Ext {raw_ext_num} (V1)")
            except requests.exceptions.HTTPError as http_err:
                if "NewCallHandlingAndForwarding" in http_err.response.text:
                    try:
                        v2_payload = transform_v1_to_v2(payload, ext_id, user_devices)
                        rc_api_call(v2_url, method=method, json=v2_payload, raise_error=True)
                        results.append(f"✅ {method} Rule Ext {raw_ext_num} (V2)")
                    except Exception as v2_err:
                        results.append(f"❌ V2 Error Ext {raw_ext_num}: {str(v2_err)}")
                else:
                    raise http_err
        except Exception as e:
            results.append(f"❌ Error Ext {raw_ext_num}: {str(e)}")

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
