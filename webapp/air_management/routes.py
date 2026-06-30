# webapp/air_management/routes.py
import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from .utils import fetch_all_assistants, parse_assistant_to_row, build_assistant_payload, build_skills_payloads

air_management_bp = Blueprint('air_management_bp', __name__, url_prefix='/api/air')

@air_management_bp.route('/audit', methods=['GET'])
@require_rc_token
@track_usage('AIR Management - Audit')
def audit_air():
    token = get_rc_access_token()
    try:
        assistants = fetch_all_assistants(token)
        
        if not assistants:
            audit_data = [{'AIR ID (Leave blank for new)': 'No Data', 'Name': 'No AI Receptionists Found'}]
        else:
            audit_data = [parse_assistant_to_row(a, token) for a in assistants]

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


@air_management_bp.route('/template', methods=['GET'])
@require_rc_token
def download_template():
    columns = [
        'AIR ID (Leave blank for new)', 'Name', 'Extension Number', 
        'Company Description', 'System Type', 'Voice Name', 'Languages', 
        'Fallback Extension ID', 'Site ID', 'Website', 'Prompt Template',
        'Idle Action (BH)', 'Idle Target (BH)', 'Idle Action (AH)', 'Idle Target (AH)',
        'Greeting (BH Text)', 'Greeting (AH Text)', 'Knowledge Base IDs',
        'Routing Rule 1', 'Routing Target 1',
        'Routing Rule 2', 'Routing Target 2',
        'Routing Rule 3', 'Routing Target 3'
    ]
    
    df_template = pd.DataFrame(columns=columns)
    
    df_template.loc[0] = {
        'AIR ID (Leave blank for new)': '',
        'Name': 'New AI Receptionist (Example)',
        'Extension Number': '8001',
        'Company Description': 'We are a dental clinic.',
        'System Type': 'PBX_VOICE',
        'Voice Name': 'Kore',
        'Languages': 'en-AU',
        'Fallback Extension ID': '1001',
        'Site ID': '',
        'Website': 'https://www.example.com',
        'Prompt Template': '',
        'Idle Action (BH)': 'Extension',
        'Idle Target (BH)': '1001',
        'Idle Action (AH)': 'Disconnect',
        'Idle Target (AH)': '',
        'Greeting (BH Text)': 'Thanks for calling Acme Corp. How can I help?',
        'Greeting (AH Text)': 'We are currently closed, but I can help you.',
        'Knowledge Base IDs': 'ctx-1234, ctx-5678',
        'Routing Rule 1': 'If they ask for billing', 'Routing Target 1': '1002',
        'Routing Rule 2': 'If they ask for emergency', 'Routing Target 2': '+1800123456',
        'Routing Rule 3': '', 'Routing Target 3': ''
    }

    instructions_data = [
        {"Column": "AIR ID", "Notes": "Leave blank to CREATE. Provide ID to UPDATE."},
        {"Column": "Fallback Extension ID", "Notes": "Required for updates. Route if AI fails."},
        {"Column": "Idle Action (BH/AH)", "Notes": "Must be exactly 'Disconnect' or 'Extension'."},
        {"Column": "Greeting (BH/AH Text)", "Notes": "Optional. The text the AI speaks upon answering."},
        {"Column": "Knowledge Base IDs", "Notes": "Optional. Comma-separated context IDs for KB grounding."},
        {"Column": "Routing Target (1-3)", "Notes": "Optional. Extension ID or E.164 external number (+1...) for context transfers."}
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
                ws.column_dimensions[column[0].column_letter].width = length + 5

    output.seek(0)
    return send_file(output, download_name="AIR_Template.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@air_management_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('AIR Management - Upload')
def upload_air():
    token = get_rc_access_token()
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
                rc_api_call(url, method="PUT", json=payload, token=token, raise_error=True)
                results.append(f"✅ Updated Base AIR: {name}")
            else:
                url = "/ai/iva/v1/accounts/~/assistants"
                new_air = rc_api_call(url, method="POST", json=payload, token=token, raise_error=True)
                air_id = new_air.get('id')
                results.append(f"✅ Created New Base AIR: {name}")

            # 2. Skills Processing
            if air_id:
                skills_to_sync = build_skills_payloads(row)
                if skills_to_sync:
                    existing_skills_resp = rc_api_call(f"/ai/iva/v1/accounts/~/assistants/{air_id}/skills", token=token, raise_error=False)
                    existing_skills = existing_skills_resp.get('records', []) if existing_skills_resp else []
                    
                    # Map existing skills by type so we know whether to PUT or POST
                    skill_map = {}
                    for s in existing_skills:
                        if 'skill' in s and 'skillType' in s['skill']:
                            skill_map[s['skill']['skillType']] = s['id']

                    for sk_type, sk_payload in skills_to_sync.items():
                        if sk_type in skill_map:
                            skill_id = skill_map[sk_type]
                            rc_api_call(f"/ai/iva/v1/accounts/~/skills/{skill_id}", method="PUT", json={"disabled": False, "skill": sk_payload}, token=token, raise_error=True)
                            results.append(f"   ↳ Updated Skill: {sk_type}")
                        else:
                            rc_api_call(f"/ai/iva/v1/accounts/~/skills", method="POST", json={"assistantId": air_id, "skill": sk_payload}, token=token, raise_error=True)
                            results.append(f"   ↳ Added New Skill: {sk_type}")

        except Exception as e:
            err_str = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try: err_str = e.response.json().get('message', err_str)
                except: pass
            results.append(f"❌ Error on '{name}': {err_str}")
            
    return jsonify({"logs": results})
