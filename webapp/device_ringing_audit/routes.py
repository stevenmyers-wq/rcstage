import time
import threading
import io
from datetime import datetime
from flask import Blueprint, jsonify, send_file, request
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
from .utils import run_audit_background, audit_progress_store, fetch_users_for_ui

device_ringing_audit_bp = Blueprint('device_ringing_audit_bp', __name__, url_prefix='/api/device_ringing_audit')

@device_ringing_audit_bp.route('/users', methods=['GET'])
@require_rc_token
def get_users():
    """Fetches list of active users to populate the selection UI"""
    token = get_rc_access_token()
    try:
        data = fetch_users_for_ui(token)
        return jsonify({'records': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@device_ringing_audit_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Device Ringing Audit')
def start_audit():
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    ext_ids = data.get('ext_ids', [])
    
    task_id = f"ringing_audit_{int(time.time())}"
    
    thread = threading.Thread(target=run_audit_background, args=(task_id, token, ext_ids))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@device_ringing_audit_bp.route('/audit/status', methods=['GET'])
@require_rc_token
def audit_status():
    task_id = request.args.get('task_id')
    data = audit_progress_store.get(task_id, {})
    
    # MAGIC FIX FOR LONG ACCOUNTS:
    # The frontend naturally hits this endpoint every 1s. 
    # @require_rc_token automatically refreshes the user's session token if it is close to expiring.
    # We inject that fresh token directly into the background task's memory state, 
    # guaranteeing the background thread's token never hits the 1-hour expiration wall!
    latest_token = get_rc_access_token()
    if latest_token and data:
        data['token'] = latest_token
    
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

@device_ringing_audit_bp.route('/debug', methods=['POST'])
@require_rc_token
def debug_extension():
    """Fetches the raw JSON for all relevant APIs to inspect the exact schema."""
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

        v2_state_rules = rc_api_call(f"/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules", token=token, return_response=True)
        v2_state_rules_json = v2_state_rules.json() if getattr(v2_state_rules, 'ok', False) else {"error": getattr(v2_state_rules, 'status_code', 'Failed')}

        return jsonify({
            "success": True,
            "extension_id": ext_id,
            "raw_data": {
                "1_devices_api": devices_json,
                "2_v1_answering_rule_api": v1_json,
                "3_v2_interaction_rules_api": v2_json,
                "4_v2_state_rules_api": v2_state_rules_json
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
