# webapp/cxone_script_analyzer/routes.py
import os
import base64
import json
from flask import Blueprint, jsonify, request, session
from functools import wraps
from webapp.usage_tracking import track_usage
from . import utils

cxone_script_analyzer_bp = Blueprint('cxone_script_analyzer_bp', __name__, url_prefix='/api/cxone')

def require_cxone_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('cxone_token') or not session.get('cxone_base_uri'):
            return jsonify({"error": "CXone authentication required."}), 401
        return f(*args, **kwargs)
    return decorated

@cxone_script_analyzer_bp.route('/auth', methods=['POST'])
@track_usage('CXone Script Analyzer Auth')
def auth():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    try:
        token, base_uri = utils.get_cxone_token(data.get('access_key'), data.get('secret_key'), data.get('region'))
        
        bu_name = "Unknown BU"
        try:
            utils.fetch_cxone_folders(base_uri, token)
            bu_name = utils.fetch_cxone_bu_name(base_uri, token)
        except Exception:
            raise Exception(f"Authentication rejected for region '{data.get('region')}'. Please double-check your Region selection.")

        # Save to Global Session
        session['cxone_token'] = token
        session['cxone_base_uri'] = base_uri
        session['cxone_region'] = data.get('region')
        session['cxone_bu_name'] = bu_name
        session.modified = True

        return jsonify({"success": True, "bu_name": bu_name})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@cxone_script_analyzer_bp.route('/status', methods=['GET'])
def cxone_status():
    return jsonify({
        "status": "connected" if session.get('cxone_token') else "disconnected",
        "region": session.get('cxone_region', 'N/A'),
        "bu_name": session.get('cxone_bu_name', '')
    }), 200

@cxone_script_analyzer_bp.route('/disconnect', methods=['POST'])
def cxone_disconnect():
    session.pop('cxone_token', None)
    session.pop('cxone_base_uri', None)
    session.pop('cxone_region', None)
    session.pop('cxone_bu_name', None)
    session.modified = True
    return jsonify({"success": True, "message": "Disconnected from CXone."}), 200

@cxone_script_analyzer_bp.route('/folders', methods=['POST', 'GET'])
@require_cxone_token
def get_folders():
    try:
        folders = utils.fetch_cxone_folders(session['cxone_base_uri'], session['cxone_token'])
        return jsonify({"success": True, "folders": folders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/scripts', methods=['POST'])
@require_cxone_token
def get_scripts():
    data = request.get_json()
    try:
        scripts = utils.fetch_cxone_scripts(session['cxone_base_uri'], session['cxone_token'], data['folder'])
        return jsonify({"success": True, "scripts": scripts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/history', methods=['POST'])
@require_cxone_token
def get_history():
    data = request.get_json()
    try:
        history = utils.fetch_script_history(session['cxone_base_uri'], session['cxone_token'], data['script_path'])
        return jsonify({"success": True, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/visualize', methods=['POST'])
@require_cxone_token
@track_usage('CXone Script Analyzer - Visualize')
def visualize():
    data = request.get_json()
    try:
        script_json_str = utils.fetch_script_content(session['cxone_base_uri'], session['cxone_token'], data['script_id'])
        graph_data = utils.generate_script_graph(script_json_str)
        return jsonify({"success": True, "graph": graph_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/analyze', methods=['POST'])
@require_cxone_token
@track_usage('CXone Script Analyzer - Generate')
def analyze():
    data = request.get_json()
    mode = data.get('mode')
    base_uri = session.get('cxone_base_uri')
    token = session.get('cxone_token')
    
    gemini_key = os.getenv('GEMINI_API_KEY')
    if not gemini_key:
        return jsonify({"error": "Server configuration error: GEMINI_API_KEY is not set in the environment variables."}), 500
    
    try:
        if mode == 'compare':
            prev_content = utils.fetch_script_content(base_uri, token, data['prev_id'])
            curr_content = utils.fetch_script_content(base_uri, token, data['curr_id'])
            prompt = utils.build_analysis_prompt(curr_content, prev_content)
            
            header = f"### Script Changelog: {data['script_name']}\n\n"
            analysis = utils.analyze_script_changes_api(prompt, gemini_key)
            final_md = header + analysis
            
        elif mode == 'as-built':
            bu_name = session.get('cxone_bu_name', 'Unknown Customer')
            scripts_data = []
            
            # Fetch content for all selected scripts
            for script in data['scripts']:
                history = utils.fetch_script_history(base_uri, token, script['path'])
                if history:
                    latest_id = history[0].get("scriptId")
                    content = utils.fetch_script_content(base_uri, token, latest_id)
                    scripts_data.append(f"### Script: {script['name']}\n{content}\n")
            
            scripts_combined = "\n".join(scripts_data)
            
            # Fetch environment config tables
            env_config_md = utils.get_environment_config_md(base_uri, token)
            
            # Call AI to generate the front matter and script analysis (with a placeholder for env config)
            prompt = utils.build_as_built_prompt(bu_name, scripts_combined)
            analysis = utils.analyze_script_changes_api(prompt, gemini_key)
            
            # Combine everything by replacing the placeholder
            if "[INJECT_ENVIRONMENT_CONFIGURATION_HERE]" in analysis:
                final_md = analysis.replace("[INJECT_ENVIRONMENT_CONFIGURATION_HERE]", env_config_md)
            else:
                # Fallback if the AI forgets the placeholder
                final_md = analysis + "\n\n# Environment Configuration\n\n" + env_config_md
                    
        elif mode == 'config':
            env_config_md = utils.get_environment_config_md(base_uri, token)
            final_md = f"# Environment Configuration\n\n{env_config_md}"
            
        else:
            return jsonify({"error": "Unsupported mode"}), 400

        pdf_bytes = utils.create_pdf(final_md)
        pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

        return jsonify({"success": True, "markdown": final_md, "pdf_b64": pdf_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500# webapp/cxone_script_analyzer/routes.py
import os
import base64
import json
from flask import Blueprint, jsonify, request, session
from functools import wraps
from webapp.usage_tracking import track_usage
from . import utils

cxone_script_analyzer_bp = Blueprint('cxone_script_analyzer_bp', __name__, url_prefix='/api/cxone')

def require_cxone_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('cxone_token') or not session.get('cxone_base_uri'):
            return jsonify({"error": "CXone authentication required."}), 401
        return f(*args, **kwargs)
    return decorated

@cxone_script_analyzer_bp.route('/auth', methods=['POST'])
@track_usage('CXone Script Analyzer Auth')
def auth():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    try:
        token, base_uri = utils.get_cxone_token(data.get('access_key'), data.get('secret_key'), data.get('region'))
        
        bu_name = "Unknown BU"
        try:
            utils.fetch_cxone_folders(base_uri, token)
            bu_name = utils.fetch_cxone_bu_name(base_uri, token)
        except Exception:
            raise Exception(f"Authentication rejected for region '{data.get('region')}'. Please double-check your Region selection.")

        # Save to Global Session
        session['cxone_token'] = token
        session['cxone_base_uri'] = base_uri
        session['cxone_region'] = data.get('region')
        session['cxone_bu_name'] = bu_name
        session.modified = True

        return jsonify({"success": True, "bu_name": bu_name})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@cxone_script_analyzer_bp.route('/status', methods=['GET'])
def cxone_status():
    return jsonify({
        "status": "connected" if session.get('cxone_token') else "disconnected",
        "region": session.get('cxone_region', 'N/A'),
        "bu_name": session.get('cxone_bu_name', '')
    }), 200

@cxone_script_analyzer_bp.route('/disconnect', methods=['POST'])
def cxone_disconnect():
    session.pop('cxone_token', None)
    session.pop('cxone_base_uri', None)
    session.pop('cxone_region', None)
    session.pop('cxone_bu_name', None)
    session.modified = True
    return jsonify({"success": True, "message": "Disconnected from CXone."}), 200

@cxone_script_analyzer_bp.route('/validate', methods=['GET'])
def cxone_validate():
    """
    Validates the CXone session with a live call to NICE — used to detect
    expired tokens that still exist in the Flask session. Called by app.js
    when navigating to CXone-dependent tool tabs.
    Returns {"valid": bool, "reason": str, "bu_name": str}.
    """
    import requests as req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    token    = session.get('cxone_token')
    base_uri = session.get('cxone_base_uri')
    bu_name  = session.get('cxone_bu_name', '')

    if not token or not base_uri:
        return jsonify({"valid": False, "reason": "not_connected"})

    try:
        response = req.get(
            f"{base_uri}/incontactapi/services/v34.0/business-unit",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=False,
            timeout=6,
        )
        if response.status_code == 401:
            return jsonify({"valid": False, "reason": "expired"})
        if response.ok:
            return jsonify({"valid": True, "bu_name": bu_name})
        return jsonify({"valid": False, "reason": f"api_error_{response.status_code}"})
    except Exception:
        return jsonify({"valid": False, "reason": "connection_error"})


@cxone_script_analyzer_bp.route('/folders', methods=['POST', 'GET'])
@require_cxone_token
def get_folders():
    try:
        folders = utils.fetch_cxone_folders(session['cxone_base_uri'], session['cxone_token'])
        return jsonify({"success": True, "folders": folders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/scripts', methods=['POST'])
@require_cxone_token
def get_scripts():
    data = request.get_json()
    try:
        scripts = utils.fetch_cxone_scripts(session['cxone_base_uri'], session['cxone_token'], data['folder'])
        return jsonify({"success": True, "scripts": scripts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/history', methods=['POST'])
@require_cxone_token
def get_history():
    data = request.get_json()
    try:
        history = utils.fetch_script_history(session['cxone_base_uri'], session['cxone_token'], data['script_path'])
        return jsonify({"success": True, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/visualize', methods=['POST'])
@require_cxone_token
@track_usage('CXone Script Analyzer - Visualize')
def visualize():
    data = request.get_json()
    try:
        script_json_str = utils.fetch_script_content(session['cxone_base_uri'], session['cxone_token'], data['script_id'])
        graph_data = utils.generate_script_graph(script_json_str)
        return jsonify({"success": True, "graph": graph_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/analyze', methods=['POST'])
@require_cxone_token
@track_usage('CXone Script Analyzer - Generate')
def analyze():
    data = request.get_json()
    mode = data.get('mode')
    base_uri = session.get('cxone_base_uri')
    token = session.get('cxone_token')
    
    gemini_key = os.getenv('GEMINI_API_KEY')
    if not gemini_key:
        return jsonify({"error": "Server configuration error: GEMINI_API_KEY is not set in the environment variables."}), 500
    
    try:
        if mode == 'compare':
            prev_content = utils.fetch_script_content(base_uri, token, data['prev_id'])
            curr_content = utils.fetch_script_content(base_uri, token, data['curr_id'])
            prompt = utils.build_analysis_prompt(curr_content, prev_content)
            
            header = f"### Script Changelog: {data['script_name']}\n\n"
            analysis = utils.analyze_script_changes_api(prompt, gemini_key)
            final_md = header + analysis
            
        elif mode == 'as-built':
            bu_name = session.get('cxone_bu_name', 'Unknown Customer')
            author = data.get('author', 'Unknown Author')
            scripts_data = []
            
            # Fetch content for all selected scripts
            for script in data['scripts']:
                history = utils.fetch_script_history(base_uri, token, script['path'])
                if history:
                    latest_id = history[0].get("scriptId")
                    content = utils.fetch_script_content(base_uri, token, latest_id)
                    scripts_data.append(f"### Script: {script['name']}\n{content}\n")
            
            scripts_combined = "\n".join(scripts_data)
            
            # Fetch environment config tables
            env_config_md = utils.get_environment_config_md(base_uri, token)
            
            # Call AI to generate the front matter and script analysis
            prompt = utils.build_as_built_prompt(bu_name, scripts_combined, author)
            analysis = utils.analyze_script_changes_api(prompt, gemini_key)
            
            # Combine everything by replacing the placeholder
            if "[INJECT_ENVIRONMENT_CONFIGURATION_HERE]" in analysis:
                final_md = analysis.replace("[INJECT_ENVIRONMENT_CONFIGURATION_HERE]", env_config_md)
            else:
                # Fallback if the AI forgets the placeholder
                final_md = analysis + "\n\n# Environment Configuration\n\n" + env_config_md
                    
        elif mode == 'config':
            env_config_md = utils.get_environment_config_md(base_uri, token)
            final_md = f"# Environment Configuration\n\n{env_config_md}"
            
        else:
            return jsonify({"error": "Unsupported mode"}), 400

        pdf_bytes = utils.create_pdf(final_md)
        pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

        return jsonify({"success": True, "markdown": final_md, "pdf_b64": pdf_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500