import io
import time
import threading
import pandas as pd
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.usage_tracking import track_usage
from .utils import fetch_all_assistants, parse_assistant_to_row, build_assistant_payload, build_skills_payloads, get_ext_directory, get_air_graph, run_transcript_export, export_progress_store

air_management_bp = Blueprint('air_management_bp', __name__, url_prefix='/api/air')

@air_management_bp.route('/list', methods=['GET'])
@require_rc_token
def list_air():
    token = get_rc_access_token()
    try:
        assistants = fetch_all_assistants(token)
        simple_list = [
            {"id": a['id'], "name": a.get('name', 'Unknown'), "ext": a.get('extensionNumber', '')} 
            for a in assistants
        ]
        return jsonify({"success": True, "assistants": simple_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@air_management_bp.route('/visualize/<air_id>', methods=['GET'])
@require_rc_token
@track_usage('AIR Management - Visualize')
def visualize_air(air_id):
    token = get_rc_access_token()
    try:
        dir_map = get_ext_directory(token)
        graph_data = get_air_graph(air_id, dir_map, token)
        return jsonify({"success": True, "graph_data": graph_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@air_management_bp.route('/audit', methods=['GET'])
@require_rc_token
@track_usage('AIR Management - Audit')
def audit_air():
    token = get_rc_access_token()
    try:
        assistants = fetch_all_assistants(token)
        
        if not assistants:
            audit_data = [{'AIR ID (Leave blank for new)': 'No Data', 'Name': 'No AI Receptionists Found'}]
            df = pd.DataFrame(audit_data)
        else:
            dir_map = get_ext_directory(token)
            audit_data = [parse_assistant_to_row(a, dir_map, token) for a in assistants]
            df = pd.DataFrame(audit_data)

            base_cols = [
                'AIR ID (Leave blank for new)', 'Name', 'Extension Number', 
                'Company Description', 'System Type', 'Voice Name', 'Languages', 
                'Fallback Extension', 'Site ID', 'Website', 'Prompt Template',
                'Idle Action (BH)', 'Idle Target (BH)', 'Idle Action (AH)', 'Idle Target (AH)',
                'Greeting (BH Text)', 'Greeting (AH Text)', 'Business Hours Schedule',
                'Booking Link', 'Sync Directory (Yes/No)', 'Directory Restricted Ext IDs', 
                'Knowledge Base IDs'
            ]
            
            faq_cols = []
            ctx_cols = []
            for col in df.columns:
                if re.match(r'^FAQ \d+', col): faq_cols.append(col)
                elif re.match(r'^Context \d+', col): ctx_cols.append(col)
            
            faq_cols.sort(key=lambda x: int(re.search(r'\d+', x).group()))
            ctx_cols.sort(key=lambda x: int(re.search(r'\d+', x).group()))
            
            final_cols = [c for c in base_cols if c in df.columns] + faq_cols + ctx_cols
            for c in df.columns:
                if c not in final_cols: final_cols.append(c)
                    
            df = df[final_cols]
        
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
        'Fallback Extension', 'Site ID', 'Website', 'Prompt Template',
        'Idle Action (BH)', 'Idle Target (BH)', 'Idle Action (AH)', 'Idle Target (AH)',
        'Greeting (BH Text)', 'Greeting (AH Text)', 'Business Hours Schedule',
        'Booking Link', 'Sync Directory (Yes/No)', 'Directory Restricted Ext IDs', 
        'Knowledge Base IDs'
    ]
    
    for i in range(1, 4): columns.extend([f'FAQ {i} Question', f'FAQ {i} Answer'])
    for i in range(1, 4): columns.extend([f'Context {i} Rule', f'Context {i} Action', f'Context {i} Target', f'Context {i} Disabled (Yes/No)'])
    
    df_template = pd.DataFrame(columns=columns)
    
    example_row = {
        'AIR ID (Leave blank for new)': '',
        'Name': 'New AI Receptionist (Example)',
        'Extension Number': '8001',
        'Company Description': 'We are a dental clinic. We help patients book appointments.',
        'System Type': 'PBX_VOICE',
        'Voice Name': 'Kore',
        'Languages': 'en-AU',
        'Fallback Extension': '1001',
        'Greeting (BH Text)': 'Thanks for calling Acme Corp. How can I help?',
        'Business Hours Schedule': '9:00AM-5:00PM Mon-Fri',
        'Sync Directory (Yes/No)': 'Yes',
        'FAQ 1 Question': 'Where are you located?',
        'FAQ 1 Answer': 'We are located at 123 George Street, Sydney.',
        'Context 1 Rule': 'If the caller asks for the billing department or an invoice.',
        'Context 1 Action': 'Extension',
        'Context 1 Target': '1002',
        'Context 1 Disabled (Yes/No)': 'No',
        'Context 2 Rule': 'If the caller has a medical emergency outside of standard appointments.',
        'Context 2 Action': 'External',
        'Context 2 Target': '+61400000000',
        'Context 2 Disabled (Yes/No)': 'No',
        'Context 3 Rule': 'If the caller requests technical support.',
        'Context 3 Action': 'Contact Centre',
        'Context 3 Target': '+611300000000',
        'Context 3 Disabled (Yes/No)': 'No'
    }
    
    for col in columns:
        if col not in example_row: example_row[col] = ''
            
    df_template.loc[0] = example_row

    instructions_data = [
        {"Column": "AIR ID", "Notes": "Leave blank to CREATE. Provide ID to UPDATE."},
        {"Column": "Fallback Extension", "Notes": "Required for updates. Provide the ID or Ext Number (e.g. '1001' or 'John Doe (Ext 1001)')."},
        {"Column": "Idle Action (BH/AH)", "Notes": "Must be exactly 'Disconnect' or 'Extension'."},
        {"Column": "Business Hours Schedule", "Notes": "Uses natural language e.g., '9:00AM-5:00PM Mon-Fri' or '24/7'."},
        {"Column": "Knowledge Base IDs", "Notes": "Comma-separated context IDs for KB grounding."},
        {"Column": "FAQ Question / Answer", "Notes": "Hardcoded responses. You can dynamically create as many FAQ columns as needed (e.g. 'FAQ 4 Question')."},
        {"Column": "Context Action", "Notes": "Must be one of: 'Extension', 'External', or 'Contact Centre'."},
        {"Column": "Context Target", "Notes": "The Extension ID/Number or E.164 external number to transfer the caller to."},
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
    token = get_rc_access_token()
    if 'file' not in request.files: return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'): df = pd.read_csv(file)
        else: df = pd.read_excel(file, sheet_name=0)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    dir_map = get_ext_directory(token)
    results = []

    for index, row in df.iterrows():
        name = row.get('Name')
        if pd.isna(name) or str(name).strip().lower() == 'nan': continue
        
        try:
            payload = build_assistant_payload(row, dir_map)
            air_id = str(row.get('AIR ID (Leave blank for new)', '')).replace('.0', '').strip()
            if air_id.lower() == 'nan': air_id = ''
            
            if air_id:
                if 'fallbackExtension' not in payload:
                    results.append(f"Row {index+2} ({name}): ⚠️ Fallback Extension is required to update.")
                    continue
                url = f"/ai/iva/v1/accounts/~/assistants/{air_id}"
                rc_api_call(url, method="PUT", json=payload, token=token, raise_error=True)
                results.append(f"✅ Updated Base AIR: {name}")
            else:
                url = "/ai/iva/v1/accounts/~/assistants"
                new_air = rc_api_call(url, method="POST", json=payload, token=token, raise_error=True)
                air_id = new_air.get('id')
                results.append(f"✅ Created New Base AIR: {name}")

            if air_id:
                skills_to_sync = build_skills_payloads(row, dir_map)
                if skills_to_sync:
                    existing_skills_resp = rc_api_call(f"/ai/iva/v1/accounts/~/assistants/{air_id}/skills", token=token, raise_error=False)
                    existing_skills = existing_skills_resp.get('records', []) if existing_skills_resp else []
                    
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

@air_management_bp.route('/transcripts/export', methods=['POST'])
@require_rc_token
@track_usage('AIR Management - Export Transcripts')
def export_transcripts():
    token = get_rc_access_token()
    data = request.get_json()
    
    date_from = data.get('date_from')
    date_to = data.get('date_to')
    air_id = data.get('air_id')
    
    if not date_from or not date_to:
        return jsonify({"error": "Start and End dates are required."}), 400
        
    task_id = f"air_export_{int(time.time())}"
    
    thread = threading.Thread(target=run_transcript_export, args=(task_id, date_from, date_to, air_id, token))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@air_management_bp.route('/transcripts/status', methods=['GET'])
@require_rc_token
def transcript_status():
    task_id = request.args.get('task_id')
    if not task_id or task_id not in export_progress_store:
        return jsonify({"error": "Invalid task ID"}), 404
        
    data = export_progress_store[task_id]
    return jsonify({
        "status": data.get("status"),
        "current": data.get("current", 0),
        "total": data.get("total", 1),
        "message": data.get("message", ""),
        "error": data.get("error", "")
    })

@air_management_bp.route('/transcripts/download', methods=['GET'])
@require_rc_token
def transcript_download():
    task_id = request.args.get('task_id')
    if not task_id or task_id not in export_progress_store:
        return "Invalid task ID", 404
        
    data = export_progress_store[task_id]
    if data.get("status") != "completed" or not data.get("file_data"):
        return "File not ready", 400
        
    mem = io.BytesIO(data['file_data'])
    return send_file(
        mem,
        as_attachment=True,
        download_name=f"AIR_Transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
