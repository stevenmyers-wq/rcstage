# webapp/visualiser/routes.py
import sys
import json
from flask import Blueprint, jsonify, request, session
from webapp.auth_utils import is_authenticated, get_rc_access_token
from webapp.rc_api import rc_api_call
from webapp.visualiser.utils import generate_mermaid_flow

viz_bp = Blueprint('visualiser', __name__)

def log_to_cloud(msg, severity="INFO"):
    """Forces logs to appear in Cloud Run immediately."""
    # Cloud Run reads stdout/stderr. We format it slightly for readability.
    if severity == "ERROR":
        print(f"[ERROR] {msg}", file=sys.stderr)
    else:
        print(f"[INFO] {msg}", file=sys.stdout)

@viz_bp.route('/api/rc/visualiser/search', methods=['GET'])
def search_for_visualiser_targets():
    """
    DIAGNOSTIC MODE: Fetches one page and DUMPS the raw response to logs.
    """
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    log_to_cloud("--- STARTING DIAGNOSTIC FETCH ---")
    
    # 1. Try to fetch just 5 extensions
    endpoint = "/restapi/v1.0/account/~/extension?perPage=5"
    log_to_cloud(f"Calling Endpoint: {endpoint}")
    
    try:
        response = rc_api_call(endpoint)
        
        # --- CRITICAL: DUMP THE RAW RESPONSE TO LOGS ---
        # This will tell us if we are getting a 403, 401, or just empty data.
        log_to_cloud(f"RAW RESPONSE TYPE: {type(response)}")
        log_to_cloud(f"RAW RESPONSE DATA: {json.dumps(response, default=str)}")
        # -----------------------------------------------

        if not response:
            log_to_cloud("Response is None/Empty. Connection Failed.", "ERROR")
            return jsonify({'status': 'success', 'results': [{'id': 'err', 'text': '❌ Connection Failed (See Logs)', 'disabled': True}]})

        if 'errorCode' in response:
             log_to_cloud(f"API returned Error Code: {response.get('errorCode')}", "ERROR")
             return jsonify({'status': 'success', 'results': [{'id': 'err', 'text': f"❌ API Error: {response.get('errorCode')}", 'disabled': True}]})
             
        records = response.get('records', [])
        log_to_cloud(f"Records found in list: {len(records)}")

        results = []
        for ext in records:
            results.append({
                'id': ext['id'],
                'text': f"✅ {ext.get('name')} (Ext: {ext.get('extensionNumber')})",
                'type': ext.get('type')
            })
            
        if not results:
             return jsonify({'status': 'success', 'results': [{'id': 'empty', 'text': '⚠️ No Extensions Found (Permissions Issue?)', 'disabled': True}]})

        return jsonify({'status': 'success', 'results': results})

    except Exception as e:
        log_to_cloud(f"EXCEPTION: {str(e)}", "ERROR")
        return jsonify({'status': 'success', 'results': [{'id': 'err', 'text': f"❌ System Error: {str(e)}", 'disabled': True}]})

@viz_bp.route('/api/rc/trace-flow/<ext_id>', methods=['GET'])
def visualize_call_flow_api(ext_id):
    # Keep existing logic for the visualize step
    if not is_authenticated() or not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
    
    session['api_log'] = []
    try:
        mermaid_graph_string = generate_mermaid_flow(ext_id)
        api_log_data = session.pop('api_log', [])
        return jsonify({'status': 'success', 'mermaid_graph': mermaid_graph_string, 'api_log': api_log_data})
    except Exception as e:
        api_log_data = session.pop('api_log', [])
        return jsonify({'status': 'error', 'message': f'Error: {str(e)}', 'api_log': api_log_data}), 500
