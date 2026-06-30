# webapp/air_management/routes.py
import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from .utils import fetch_all_assistants, parse_assistant_to_row, build_assistant_payload

air_management_bp = Blueprint('air_management', __name__)

@air_management_bp.route('/api/air/audit', methods=['GET'])
@require_rc_token
@track_usage('AIR Management Audit')
def audit_air():
    token = get_rc_access_token()
    try:
        assistants = fetch_all_assistants(token)
        
        if not assistants:
            audit_data = [{'ID (Leave blank for new)': 'No Data', 'Name': 'No AI Receptionists Found'}]
        else:
            audit_data = [parse_assistant_to_row(a) for a in assistants]

        df = pd.DataFrame(audit_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='AIR Audit')
            worksheet = writer.sheets['AIR Audit']
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = length + 5
        
        output.seek(0)
        return send_file(
            output, 
            download_name=f"AIR_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx", 
            as_attachment=True, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@air_management_bp.route('/api/air/template', methods=['GET'])
def download_template():
    columns = [
        'ID (Leave blank for new)', 'Name', 'Extension Number', 
        'Company Name', 'Company Description', 'System Type', 'Voice Name', 
        'Languages', 'Time Zone', 'Fallback Extension ID', 'Site ID',
        'Website', 'Prompt Template', 'Tools Version'
    ]
    
    df_template = pd.DataFrame([], columns=columns)
    
    # 1. Provide an example row showing the expected values
    example_row = {
        'ID (Leave blank for new)': '',
        'Name': 'Main AI Receptionist',
        'Extension Number': '8000',
        'Company Name': 'Acme Corp',
        'Company Description': 'We are a leading law firm based in Sydney.',
        'System Type': 'PBX_VOICE',
        'Voice Name': 'Kore',
        'Languages': 'en-AU, en-US',
        'Time Zone': 'Australia/Sydney',
        'Fallback Extension ID': '101',
        'Site ID': 'main-site',
        'Website': 'https://example.com',
        'Prompt Template': '',
        'Tools Version': ''
    }
    df_template.loc[0] = example_row

    # 2. Add an instructions sheet
    instructions_data = [
        {"Column": "ID (Leave blank for new)", "Notes": "Do NOT edit this if updating. Leave completely blank to create a new AIR."},
        {"Column": "Name", "Notes": "The display name for the extension (e.g. 'Support AI'). Required."},
        {"Column": "Extension Number", "Notes": "The numeric extension for the AIR. Required."},
        {"Column": "Fallback Extension ID", "Notes": "The internal RingCentral Extension ID where calls route if the AI fails or the user requests a human. Required for updates."},
        {"Column": "System Type", "Notes": "Must be one of: PBX_VOICE, VM_VOICE, RING_CX_VOICE, RING_CX_TEXT. PBX_VOICE is standard."},
        {"Column": "Voice Name", "Notes": "The AI actor voice (e.g., 'Kore', 'Puck', 'Aoede')."},
        {"Column": "Languages", "Notes": "Comma separated BCP-47 tags. e.g., 'en-AU, en-US'."},
        {"Column": "Time Zone", "Notes": "IANA time zone string. e.g., 'Australia/Sydney'."}
    ]
    df_instructions = pd.DataFrame(instructions_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_template.to_excel(writer, index=False, sheet_name='AIR Template')
        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        
        # Adjust column widths
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for column in ws.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                ws.column_dimensions[column[0].column_letter].width = length + 5

    output.seek(0)
    return send_file(
        output, 
        download_name="AIR_Template.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@air_management_bp.route('/api/air/upload', methods=['POST'])
@require_rc_token
@track_usage('AIR Management Update')
def upload_air():
    token = get_rc_access_token()
    if 'file' not in request.files: 
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'): df = pd.read_csv(file)
        else: df = pd.read_excel(file, sheet_name=0) # Only process the first sheet (skip instructions)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    for index, row in df.iterrows():
        name = row.get('Name')
        if pd.isna(name): continue
        
        try:
            payload = build_assistant_payload(row)
            air_id = str(row.get('ID (Leave blank for new)', '')).replace('.0', '').strip()
            
            if air_id and air_id.lower() != 'nan':
                # Update requires fallbackExtension
                if 'fallbackExtension' not in payload:
                    results.append(f"Row {index+2} ({name}): ⚠️ Fallback Extension ID is required for updating an existing AIR.")
                    continue
                
                url = f"/ai/iva/v1/accounts/~/assistants/{air_id}"
                rc_api_call(url, method="PUT", json=payload, token=token, raise_error=True)
                results.append(f"✅ Updated AIR: {name}")
            else:
                # Create new
                url = "/ai/iva/v1/accounts/~/assistants"
                rc_api_call(url, method="POST", json=payload, token=token, raise_error=True)
                results.append(f"✅ Created New AIR: {name}")
                
        except Exception as e:
            err_str = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    err_str = e.response.json().get('message', err_str)
                except:
                    pass
            results.append(f"❌ Error on '{name}': {err_str}")
            
    return jsonify({"logs": results})
