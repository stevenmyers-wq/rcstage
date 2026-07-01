import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from .utils import fetch_all_assistants, parse_assistant_to_row, build_assistant_payload, build_skills_payloads

air_management_bp = Blueprint('air_management_bp', __name__, url_prefix='/api/air')

@air_management_bp.route('/audit', methods=['GET'])
@require_rc_token
@track_usage('AIR Management - Audit')
def audit_air():
    try:
        assistants = fetch_all_assistants()
        
        if not assistants:
            audit_data = [{'AIR ID (Leave blank for new)': 'No Data', 'Name': 'No AI Receptionists Found'}]
        else:
            audit_data = [parse_assistant_to_row(a) for a in assistants]

        df = pd.DataFrame(audit_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='AIR Audit')
            worksheet = writer.sheets['AIR Audit']
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
        
        output.seek(0)
        return send_file(
            output, 
            download_name=f"AIR_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx", 
            as_attachment=True, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@air_management_bp.route('/template', methods=['GET'])
@require_rc_token
def download_template():
    columns = [
        'AIR ID (Leave blank for new)', 'Name', 'Extension Number', 
        'Company Description', 'System Type', 'Voice Name', 'Languages', 
        'Fallback Extension ID', 'Site ID', 'Website', 'Prompt Template',
        'Idle Action (BH)', 'Idle Target (BH)', 'Idle Action (AH)', 'Idle Target (AH)',
        'Greeting (BH Text)', 'Greeting (AH Text)', 'Business Hours Schedule',
        'Booking Link', 'Sync Directory (Yes/No)', 'Directory Restricted Ext IDs', 
        'Knowledge Base IDs',
        'FAQ 1 Question', 'FAQ 1 Answer',
        'FAQ 2 Question', 'FAQ 2 Answer',
        'FAQ 3 Question', 'FAQ 3 Answer'
    ]
    
    # Add the 10 context columns dynamically
    for i in range(1, 11):
        columns.extend([f'Context {i} Rule', f'Context {i} Target', f'Context {i} Disabled (Yes/No)'])
    
    df_template = pd.DataFrame(columns=columns)
    
    example_row = {
        'AIR ID (Leave blank for new)': '',
        'Name': 'New AI Receptionist (Example)',
        'Extension Number': '8001',
        'Company Description': 'We are a dental clinic.',
        'System Type': 'PBX_VOICE',
        'Voice Name': 'Kore',
        'Languages': 'en-AU',
        'Fallback Extension ID': '1001',
        'Greeting (BH Text)': 'Thanks for calling. How can I help?',
        'Context 1 Rule': 'If the caller asks for the billing department or has a question about an invoice.',
        'Context 1 Target': '1002',
        'Context 1 Disabled (Yes/No)': 'No',
        'Context 2 Rule': 'If the caller has a medical emergency outside of standard appointments.',
        'Context 2 Target': '+61400000000',
        'Context 2 Disabled (Yes/No)': 'No'
    }
    
    # Fill remaining columns with empty strings
    for col in columns:
        if col not in example_row:
            example_row[col] = ''
            
    df_template.loc[0] = example_row

    instructions_data = [
        {"Column": "AIR ID", "Notes": "Leave blank to CREATE. Provide ID to UPDATE."},
        {"Column": "Fallback Extension ID", "Notes": "Required for updates. Route if AI fails."},
        {"Column": "Idle Action (BH/AH)", "Notes": "Must be exactly 'Disconnect' or 'Extension'."},
        {"Column": "Business Hours Schedule", "Notes": "Uses natural language e.g., '9:00AM-5:00PM Mon-Fri' or '24/7'."},
        {"Column": "Sync Directory", "Notes": "'Yes' allows the AI to transfer calls by name to staff members."},
        {"Column": "Knowledge Base IDs", "Notes": "Comma-separated context IDs for KB grounding."},
        {"Column": "FAQ Question / Answer", "Notes": "Hardcoded responses for specific questions."},
        {"Column": "Context Rule (1-10)", "Notes": "The natural language instruction for the AI (e.g., 'If they ask for sales'). Maps to 'Transfer by Context'."},
        {"Column": "Context Target (1-10)", "Notes": "The Extension ID or E.164 external number to transfer the caller to if the rule is triggered."},
        {"Column": "Context Disabled", "Notes": "'Yes' disables the routing rule without deleting it. Defaults to 'No'."}
    ]
    df_instructions = pd.DataFrame(instructions_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_template.to_excel(writer, index=False, sheet_name='AIR Template')
        df_instructions.to_excel(writer, index=False, sheet_name='Format Guide')
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for column in ws.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                ws.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

    output.seek(0)
    return send_file(output, download_name="AIR_Template.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@air_management_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('AIR Management - Upload')
def upload_air():
    if 'file' not in request.files: return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'): df = pd.read_csv(file)
        else: df = pd.read_excel(file, sheet_name=0)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    for index, row in df.iterrows():
        name = row.get('Name')
        if pd.isna(name): continue
        
        try:
            # 1. Base Assistant Payload
            payload = build_assistant_payload(row)
            air_id = str(row.get('AIR ID (Leave blank for new)', '')).replace('.0', '').strip()
            
            if air_id and air_id.lower() != 'nan':
                if 'fallbackExtension' not in payload:
                    results.append(f"Row {index+2} ({name}): ⚠️ Fallback Extension ID is required to update.")
                    continue
                url = f"/ai/iva/v1/accounts/~/assistants/{air_id}"
                rc_api_call(url, method="PUT", json=payload, raise_error=True)
                results.append(f"✅ Updated Base AIR: {name}")
            else:
                url = "/ai/iva/v1/accounts/~/assistants"
                new_air = rc_api_call(url, method="POST", json=payload, raise_error=True)
                air_id = new_air.get('id')
                results.append(f"✅ Created New Base AIR: {name}")

            # 2. Skills Processing
            if air_id:
                skills_to_sync = build_skills_payloads(row)
                if skills_to_sync:
                    existing_skills_resp = rc_api_call(f"/ai/iva/v1/accounts/~/assistants/{air_id}/skills", raise_error=False)
                    existing_skills = existing_skills_resp.get('records', []) if existing_skills_resp else []
                    
                    skill_map = {}
                    for s in existing_skills:
                        if 'skill' in s and 'skillType' in s['skill']:
                            skill_map[s['skill']['skillType']] = s['id']

                    for sk_type, sk_payload in skills_to_sync.items():
                        if sk_type in skill_map:
                            skill_id = skill_map[sk_type]
                            rc_api_call(f"/ai/iva/v1/accounts/~/skills/{skill_id}", method="PUT", json={"disabled": False, "skill": sk_payload}, raise_error=True)
                            results.append(f"   ↳ Updated Skill: {sk_type}")
                        else:
                            rc_api_call(f"/ai/iva/v1/accounts/~/skills", method="POST", json={"assistantId": air_id, "skill": sk_payload}, raise_error=True)
                            results.append(f"   ↳ Added New Skill: {sk_type}")

        except Exception as e:
            err_str = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try: err_str = e.response.json().get('message', err_str)
                except: pass
            results.append(f"❌ Error on '{name}': {err_str}")
            
    return jsonify({"logs": results})
