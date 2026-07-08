import time
import threading
import io
from datetime import datetime
from flask import Blueprint, jsonify, send_file, request
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
from .utils import run_audit_background, audit_progress_store

device_ringing_audit_bp = Blueprint('device_ringing_audit_bp', __name__, url_prefix='/api/device_ringing_audit')

@device_ringing_audit_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Device Ringing Audit')
def start_audit():
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    
    task_id = f"ringing_audit_{int(time.time())}"
    
    thread = threading.Thread(target=run_audit_background, args=(task_id, token))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@device_ringing_audit_bp.route('/audit/status', methods=['GET'])
@require_rc_token
def audit_status():
    task_id = request.args.get('task_id')
    data = audit_progress_store.get(task_id, {})
    
    safe_data = {
        'current': data.get('current', 0),
        'total': data.get('total', 0),
        'status': data.get('status', 'running'),
        'message': data.get('message', 'Initializing...'),
        'error': data.get('error', '')
    }
    return jsonify(safe_data)

@device_ringing_audit_bp.route('/audit/download', methods=['GET'])
@require_rc_token
def audit_download():
    task_id = request.args.get('task_id')
    data = audit_progress_store.get(task_id, {})
    
    if data.get('status') == 'completed' and 'file_data' in data:
        mem = io.BytesIO(data['file_data'])
        filename = f"Device_Ringing_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return send_file(
            mem, 
            as_attachment=True, 
            download_name=filename, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return "File not ready or expired", 404

# --- DEBUG ROUTE ---
@device_ringing_audit_bp.route('/debug', methods=['POST'])
@require_rc_token
def debug_extension():
    """Fetches the raw JSON for all relevant APIs so we can inspect the exact schema."""
    token = get_rc_access_token()
    data = request.get_json()
    ext_num = data.get('extension_number')
    
    if not ext_num:
        return jsonify({"error": "Please provide an extension number."}), 400

    try:
        search_resp = rc_api_call(f"/restapi/v1.0/account/~/extension?extensionNumber={ext_num}", token=token)
        records = search_resp.get('records', [])
        if not records:
            return jsonify({"error": f"Extension {ext_num} not found."}), 404
            
        ext_id = records[0]['id']
        
        devices = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/device", token=token, return_response=True)
        devices_json = devices.json() if getattr(devices, 'ok', False) else {"error": getattr(devices, 'status_code', 'Failed')}
        
        v1_rule = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule", token=token, return_response=True)
        v1_json = v1_rule.json() if getattr(v1_rule, 'ok', False) else {"error": getattr(v1_rule, 'status_code', 'Failed')}
        
        v2_interaction = rc_api_call(f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/interaction-rules", token=token, return_response=True)
        v2_json = v2_interaction.json() if getattr(v2_interaction, 'ok', False) else {"error": getattr(v2_interaction, 'status_code', 'Failed')}

        # CHANGED: Hits the correct "state-rules/work-hours" endpoint
        v2_work_hours = rc_api_call(f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules/work-hours", token=token, return_response=True)
        v2_work_hours_json = v2_work_hours.json() if getattr(v2_work_hours, 'ok', False) else {"error": getattr(v2_work_hours, 'status_code', 'Failed')}

        return jsonify({
            "success": True,
            "extension_id": ext_id,
            "raw_data": {
                "1_devices_api": devices_json,
                "2_v1_answering_rule_api": v1_json,
                "3_v2_interaction_rules_api": v2_json,
                "4_v2_work_hours_state_api": v2_work_hours_json
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
