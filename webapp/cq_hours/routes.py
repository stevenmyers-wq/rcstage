import os
import io
import json
import threading
import time
import pandas as pd
from flask import Blueprint, jsonify, request, send_file, session, Response, stream_with_context, render_template
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

cq_hours_bp = Blueprint('cq_hours', __name__, url_prefix='/api/cq_hours')
cq_hours_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])

@cq_hours_bp.route('/help', methods=['GET'])
def help_page():
    """Standalone reference page documenting Call Queue Manager behaviour."""
    return render_template('cq_hours_help.html')

@cq_hours_bp.route('/template', methods=['GET'])
def download_template():
    df = pd.DataFrame([
        {
            "Queue Name": "Support Queue",
            "Record Group Name": "",
            "Extension": "1001",
            "Site": "Main Site",
            "Status": "Enabled",
            "Phone Number": "",
            "Queue Manager": "",
            "Queue Email": "support@company.com",
            "Queue PIN": "",
            "Members (Ext)": "2001, 2002, 2003",
            "Timezone": "US/Eastern",
            "Hours": "8:30AM-5:30PM Mon-Fri",
            "Greeting": "",
            "Audio While Connecting": "",
            "Hold Music": "",
            "Interrupt Audio": "30 Seconds",
            "Interrupt Prompt": "Thank you for your patience",
            "Ring Type": "Simultaneous",
            "User Ring Time": "20 Seconds",
            "Total Ring Time": "5 Minutes",
            "Wrap Up Time": "15 Seconds",
            "Member Queue Status": "Allowed",
            "Callers In Queue": "10",
            "When Queue is Full": "TransferToExtension",
            "Queue Full Destination": "2004",
            "When Max Time is Reached": "Voicemail",
            "Time Reached Destination": "",
            "Voicemail Greeting": "",
            "Voicemail Recipients": "",
            "Voicemail Notifications": "Notify & Attach",
            "Voicemail Notifications Email": "manager@company.com",
            "After Hours Behavior": "TransferToExtension",
            "After Hours Destination": "2004",
            "Voicemail to Text": "On",
            "Missed Call Notifications": "Off",
            "Inbound Fax Notifications": "Off",
            "Outbound Fax Notifications": "Off",
            "Text Notifications": "Off",
            "Missed Call Notifications Email": "",
            "Inbound Fax Notifications Email": "",
            "Outbound Fax Notifications Email": "",
            "Text Notifications Email": ""
        }
    ])
    df = df[utils.TEMPLATE_COLUMNS]

    output = utils.build_config_workbook(df)
    return send_file(output, as_attachment=True, download_name='Call_Queue_Manager_Template.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@cq_hours_bp.route('/queues', methods=['GET'])
def get_queues():
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"success": False, "error": "Unauthorized. Please bridge connection."}), 401
    try:
        queues = utils.fetch_all_queues(token)
        return jsonify({"success": True, "queues": queues})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@cq_hours_bp.route('/audit', methods=['POST'])
@track_usage('CQ Omni Manager Audit')
def start_audit():
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.json
    queue_ids = data.get('queue_ids', [])
    if not queue_ids:
        return jsonify({"success": False, "error": "No queues selected."}), 400
        
    task_id = f"audit_{int(time.time())}"
    threading.Thread(target=utils.run_cq_audit, args=(task_id, queue_ids, token)).start()
    
    return jsonify({"success": True, "task_id": task_id})

@cq_hours_bp.route('/audit/status', methods=['GET'])
def audit_status():
    task_id = request.args.get('task_id')
    data = utils.audit_progress_store.get(task_id, {})
    
    safe_data = {
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'file_ready': data.get('file_ready', False),
        'error': data.get('error', '')
    }
    return jsonify(safe_data)

@cq_hours_bp.route('/audit/download', methods=['GET'])
def audit_download():
    task_id = request.args.get('task_id')
    data = utils.audit_progress_store.get(task_id, {})
    
    if data.get('file_ready') and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        return send_file(
            mem, 
            as_attachment=True, 
            download_name='Call_Queue_Manager_Export.xlsx', 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return "File not ready or expired", 404

@cq_hours_bp.route('/sheets', methods=['POST'])
def get_sheets():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
        
    file = request.files['file']
    if file.filename.endswith('.csv'):
        return jsonify(["CSV Format (No Sheets)"])
        
    try:
        xls = pd.ExcelFile(file)
        return jsonify(xls.sheet_names)
    except Exception as e:
        return jsonify({"error": f"Failed to parse sheets: {str(e)}"}), 400

@cq_hours_bp.route('/upload', methods=['POST'])
@track_usage('CQ Omni Manager Update')
def upload_hours():
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"type": "error", "message": "Unauthorized: Please Bridge the connection first."}), 401

    if 'file' not in request.files:
        return jsonify({"type": "error", "message": "No file uploaded."}), 400
        
    is_preview = request.form.get('action') == 'preview'
    sheet_name = request.form.get('sheet_name')
    wipe_members = request.form.get('wipe_members') == '1'
    override_managers = request.form.get('override_managers') == '1'
    task_id = request.form.get('task_id')

    try:
        file = request.files['file']
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            if sheet_name and sheet_name != 'CSV Format (No Sheets)':
                df = pd.read_excel(file, sheet_name=sheet_name)
            else:
                df = pd.read_excel(file)
                
        df = df.fillna('')
        records = df.to_dict('records')
    except Exception as e:
        return jsonify({"type": "error", "message": f"File parsing error: {str(e)}"}), 400

    def generate():
        try:
            for chunk in utils.update_cq_batch(records, token, is_preview=is_preview, wipe_members=wipe_members, task_id=task_id, override_managers=override_managers):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp
