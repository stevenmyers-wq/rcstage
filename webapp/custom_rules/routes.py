import io
import json
import pandas as pd
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from openpyxl.worksheet.datavalidation import DataValidation
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
    try:
        ext_resp = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000, 'type': 'User'})
        if not ext_resp or 'records' not in ext_resp:
            return jsonify({"error": "Failed to fetch extensions list"}), 500
        
        extensions = ext_resp['records']
        audit_data = []

        for ext in extensions:
            ext_id = ext['id']
            if ext['status'] == 'Disabled': continue

            rules_found = False

            # Try V2
            try:
                v2_url = f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules"
                v2_resp = rc_api_call(v2_url, raise_error=True)
                if v2_resp and 'records' in v2_resp:
                    for rule in v2_resp['records']:
                        row = parse_rule_to_row(ext, rule, is_v2=True)
                        audit_data.append(row)
                    rules_found = True
            except Exception:
                pass 

            # Fallback V1
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

        if not audit_data:
            audit_data = [{'Ext Number': 'No Data', 'Rule Name': 'No Custom Rules Found'}]

        df = pd.DataFrame(audit_data)
        
        cols = ['Ext Number', 'Ext Name', 'Rule ID', 'Rule Name', 'Enabled', 
                'Caller ID', 'Called Number', 
                'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'Specific Dates',
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

# --- UPDATE ROUTE ---
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

# --- TEMPLATE DOWNLOAD ROUTE (UPDATED) ---
@custom_rules_bp.route('/api/custom_rules/template', methods=['GET'])
def download_template():
    # 1. Define Template Columns
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 
        'Caller ID', 'Called Number', 
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 
        'Specific Dates', 
        'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient'
    ]
    
    # 2. Define Instructions / Examples
    instructions_data = [
        {"Field": "Ext Number", "Required": "Yes", "Format": "101", "Notes": "The extension the rule belongs to."},
        {"Field": "Rule ID", "Required": "No", "Format": "123456", "Notes": "Leave BLANK to create a NEW rule. Fill to UPDATE an existing rule."},
        {"Field": "Caller ID", "Required": "No", "Format": "+61400123456", "Notes": "Incoming numbers to match. Comma-separated."},
        {"Field": "Called Number", "Required": "No", "Format": "+61299990000", "Notes": "The DID the caller dialed. Comma-separated."},
        {"Field": "Days (Mon-Sun)", "Required": "No", "Format": "9:00 AM - 5:00 PM", "Notes": "12-hour format with AM/PM. Separate multiple ranges with commas: '9:00 AM - 12:00 PM, 1:00 PM - 5:00 PM'"},
        {"Field": "Specific Dates", "Required": "No", "Format": "YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM", "Notes": "Example: '2024-12-25 00:00 to 2024-12-26 23:59'. Separate multiple date ranges with commas."},
        {"Field": "Action", "Required": "Yes", "Format": "Select from Dropdown", "Notes": "Use the dropdown box provided in the Template sheet."},
        {"Field": "External Number", "Required": "If Action=Transfer", "Format": "+614...", "Notes": "E.164 format preferred."},
        {"Field": "Enabled", "Required": "No", "Format": "Select from Dropdown", "Notes": "Defaults to Yes."}
    ]

    df_template = pd.DataFrame([], columns=columns)
    df_instructions = pd.DataFrame(instructions_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Template
        df_template.to_excel(writer, index=False, sheet_name='Template')
        ws1 = writer.sheets['Template']
        
        # --- Add Dropdown Validations ---
        # Column E is Enabled, Column P is Action
        dv_enabled = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
        dv_action = DataValidation(type="list", formula1='"Transfer to External,Transfer to Extension,Send to Voicemail,Play Message,Play Message and Disconnect,Fwd Direct To Main"', allow_blank=True)
        
        ws1.add_data_validation(dv_enabled)
        ws1.add_data_validation(dv_action)
        
        # Apply to a generous range of rows (Row 2 to 1000)
        dv_enabled.add("E2:E1000")
        dv_action.add("P2:P1000")

        # Auto-adjust column widths
        for column in ws1.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            ws1.column_dimensions[column[0].column_letter].width = length + 5

        # Sheet 2: Format Guide
        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        ws2 = writer.sheets['Format Guide']
        for column in ws2.columns:
            length = max(len(str(cell.value) or "") for cell in column)
            ws2.column_dimensions[column[0].column_letter].width = length + 10

    output.seek(0)
    return send_file(
        output, 
        download_name="custom_rules_template.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
