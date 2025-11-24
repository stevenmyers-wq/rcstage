import io
from flask import send_file
from flask import Blueprint, request, jsonify, render_template
import pandas as pd
from .utils import build_rule_payload
# Import your shared RC client helper
# from webapp.rc_api import get_platform 

custom_rules_bp = Blueprint('custom_rules', __name__)

def get_extension_id(platform, extension_number):
    try:
        resp = platform.get('/restapi/v1.0/account/~/extension', {'extensionNumber': extension_number})
        records = resp.json().get('records', [])
        return records[0]['id'] if records else None
    except:
        return None

@custom_rules_bp.route('/api/update_rules', methods=['POST'])
def update_rules():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']
    try:
        df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    # GET PLATFORM (Assuming you have a helper for this based on user session)
    # platform = get_platform() 
    # For now, assuming you pass it or handle auth here
    
    results = []
    
    for index, row in df.iterrows():
        ext_num = row.get('Ext Number')
        if pd.isna(ext_num): continue

        # 1. Resolve Extension ID
        ext_id = get_extension_id(platform, ext_num)
        if not ext_id:
            results.append(f"Row {index}: Ext {ext_num} not found.")
            continue

        # 2. Build Payload
        payload, action_type = build_rule_payload(row, ext_id)

        # 3. Resolve Action Targets (Requires API calls for IDs)
        if action_type == 'UnconditionalForwarding' and pd.notna(row.get('External Number')):
            payload['unconditionalForwarding'] = {'phoneNumber': str(row.get('External Number'))}
        elif action_type == 'TransferToExtension' and pd.notna(row.get('Transfer Extension')):
            target_id = get_extension_id(platform, row.get('Transfer Extension'))
            if target_id: payload['transfer'] = {'extension': {'id': target_id}}
        elif action_type == 'TakeMessagesOnly' and pd.notna(row.get('Voicemail Recipient')):
            vm_id = get_extension_id(platform, row.get('Voicemail Recipient'))
            if vm_id: payload['voicemail'] = {'recipient': {'id': vm_id}}

        # 4. Send to RC API
        try:
            rule_id = row.get('Rule ID')
            if pd.notna(rule_id) and str(rule_id).strip():
                platform.put(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{rule_id}", payload)
                results.append(f"✅ Updated {ext_num}")
            else:
                platform.post(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule", payload)
                results.append(f"✅ Created for {ext_num}")
        except Exception as e:
            results.append(f"❌ Error {ext_num}: {str(e)}")


    return jsonify({"logs": results})
@custom_rules_bp.route('/template', methods=['GET'])
def download_template():
    # Define the exact columns your script expects
    columns = [
        'Ext Number', 'Ext Name', 'Rule Name', 'Rule ID', 'Enabled', 
        'Caller ID', 'Called Number', 'Work or After Hours', 
        'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 
        'Specific Dates', 'Action', 'Transfer Extension', 'External Number', 'Voicemail Recipient'
    ]

    # Create an example row
    example_data = [{
        'Ext Number': '101',
        'Ext Name': 'John Doe',
        'Rule Name': 'Holiday Rule',
        'Rule ID': '', 
        'Enabled': 'Yes',
        'Caller ID': '1234567890',
        'Called Number': '',
        'Work or After Hours': '',
        'Monday': '9:00 AM - 5:00 PM',
        'Tuesday': '9:00 AM - 5:00 PM',
        'Action': 'Transfer to External',
        'External Number': '15550001234'
    }]

    # Create DataFrame
    df = pd.DataFrame(example_data, columns=columns)

    # Create an in-memory Excel file
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template')
        
        # Auto-adjust column widths
        worksheet = writer.sheets['Template']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value) or "") for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2

    output.seek(0)
    
    return send_file(
        output, 
        download_name="custom_rules_template.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
