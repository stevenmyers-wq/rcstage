import io
import json
import threading
import time
import pandas as pd
from flask import Blueprint, jsonify, request, send_file, session, Response, stream_with_context
from webapp.usage_tracking import track_usage
from openpyxl.worksheet.datavalidation import DataValidation
from . import utils

cq_hours_bp = Blueprint('cq_hours', __name__, url_prefix='/api/cq_hours')

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
            "Interrupt Audio": "Periodically",
            "Interrupt Prompt": "30 Seconds",
            "Ring Type": "Simultaneous",
            "User Ring Time": "20 Seconds",
            "Total Ring Time": "5 Minutes",
            "Wrap Up Time": "15 Seconds",
            "Member Queue Status": "",
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
            "After Hours Destination": "2004"
        }
    ])
    
    global_timezones = [
        "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii",
        "Canada/Eastern", "Canada/Central", "Canada/Mountain", "Canada/Pacific", "Canada/Atlantic",
        "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Athens", "Europe/Moscow",
        "GMT", "UTC", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Asia/Hong_Kong",
        "Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth",
        "Pacific/Auckland", "America/Sao_Paulo", "America/Buenos_Aires", "America/Mexico_City"
    ]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Queue Config')
        
        tz_df = pd.DataFrame({"ValidTimezones": global_timezones})
        tz_df.to_excel(writer, index=False, sheet_name='Timezone_Ref')
        
        workbook = writer.book
        config_ws = workbook['Queue Config']
        
        dv_tz = DataValidation(type="list", formula1="=Timezone_Ref!$A$2:$A$" + str(len(global_timezones) + 1), allow_blank=True)
        config_ws.add_data_validation(dv_tz)
        dv_tz.add("K2:K1000") 
        
        schema_validations = {
            "E": '"Enabled,Disabled"', "M": '"Default,Custom,Off"', "N": '"Default,Custom,Off"', "O": '"Default,Custom,Off"',
            "P": '"Periodically,Never"', "Q": '"10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute"',
            "R": '"Simultaneous,Sequential,Rotating"', "S": '"10 Seconds,15 Seconds,20 Seconds,25 Seconds,30 Seconds,40 Seconds,50 Seconds,1 Minute,2 Minutes"',
            "T": '"15 Seconds,30 Seconds,45 Seconds,1 Minute,2 Minutes,3 Minutes,4 Minutes,5 Minutes,10 Minutes,15 Minutes"',
            "U": '"0 Seconds,5 Seconds,10 Seconds,15 Seconds,20 Seconds,30 Seconds,1 Minute"', "V": '"Accepting,NotAccepting"',
            "W": '"1,2,3,4,5,10,15,20,25"', "X": '"Voicemail,TransferToExtension,Disconnect,Announcement"',
            "Z": '"Voicemail,TransferToExtension,Disconnect,Announcement"', "AB": '"Default,Custom,Off"',
            "AD": '"Off,Notify by Email,Notify & Attach,Notify Attach & Read"',
            "AF": '"TakeMessagesOnly,TransferToExtension,UnconditionalForwarding,PlayAnnouncementOnly,Disconnect"'
        }

        for col_letter, formula_string in schema_validations.items():
            dv = DataValidation(type="list", formula1=formula_string, allow_blank=True)
            config_ws.add_data_validation(dv)
            dv.add(f"{col_letter}2:{col_letter}1000")

    output.seek(0)
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
    return jsonify(data)

@cq_hours_bp.route('/audit/download', methods=['GET'])
def audit_download():
    task_id = request.args.get('task_id')
    data = utils.audit_progress_store.get(task_id, {})
    if data.get('file_ready'):
        mem = io.BytesIO(data['file_data'])
        return send_file(
            mem, 
            as_attachment=True, 
            download_name='Call_Queue_Manager_Export.xlsx', 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return "File not ready", 404

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
            for chunk in utils.update_cq_batch(records, token, is_preview=is_preview):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp
