# webapp/cxone_script_analyzer/routes.py
import os
import base64
from flask import Blueprint, jsonify, request
from webapp.usage_tracking import track_usage
from . import utils

cxone_script_analyzer_bp = Blueprint('cxone_script_analyzer_bp', __name__, url_prefix='/api/cxone')

@cxone_script_analyzer_bp.route('/auth', methods=['POST'])
@track_usage('CXone Script Analyzer')
def auth():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    try:
        token, base_uri = utils.get_cxone_token(data.get('access_key'), data.get('secret_key'), data.get('region'))
        return jsonify({"success": True, "token": token, "base_uri": base_uri})
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@cxone_script_analyzer_bp.route('/folders', methods=['POST'])
def get_folders():
    data = request.get_json()
    try:
        folders = utils.fetch_cxone_folders(data['base_uri'], data['token'])
        return jsonify({"success": True, "folders": folders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/scripts', methods=['POST'])
def get_scripts():
    data = request.get_json()
    try:
        scripts = utils.fetch_cxone_scripts(data['base_uri'], data['token'], data['folder'])
        return jsonify({"success": True, "scripts": scripts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/history', methods=['POST'])
def get_history():
    data = request.get_json()
    try:
        history = utils.fetch_script_history(data['base_uri'], data['token'], data['script_path'])
        return jsonify({"success": True, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cxone_script_analyzer_bp.route('/analyze', methods=['POST'])
@track_usage('CXone Script Analyzer - Generate')
def analyze():
    data = request.get_json()
    mode = data.get('mode')
    base_uri = data.get('base_uri')
    token = data.get('token')
    
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
            final_md = f"# CXone Script As-Built Documentation\n\n"
            for script in data['scripts']:
                history = utils.fetch_script_history(base_uri, token, script['path'])
                if history:
                    latest_id = history[0].get("scriptId")
                    content = utils.fetch_script_content(base_uri, token, latest_id)
                    prompt = utils.build_as_built_prompt(content)
                    analysis = utils.analyze_script_changes_api(prompt, gemini_key)
                    final_md += f"## Script: {script['name']}\n{analysis}\n\n---\n\n"
        else:
            return jsonify({"error": "Unsupported mode"}), 400

        pdf_bytes = utils.create_pdf(final_md)
        pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

        return jsonify({"success": True, "markdown": final_md, "pdf_b64": pdf_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500