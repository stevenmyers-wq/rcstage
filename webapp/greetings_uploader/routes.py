from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from .utils import get_message_extensions, upload_greeting_to_extension, set_directory_visibility
from requests.exceptions import HTTPError

greetings_uploader_bp = Blueprint('greetings_uploader_bp', __name__)

@greetings_uploader_bp.route('/api/greetings_uploader/extensions', methods=['GET'])
@require_rc_token
def api_get_target_extensions():
    try:
        extensions = get_message_extensions()
        return jsonify({"extensions": extensions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@greetings_uploader_bp.route('/api/greetings_uploader/upload', methods=['POST'])
@require_rc_token
@track_usage('Greetings - Upload Audio')
def api_upload_greeting():
    if 'audio_file' not in request.files:
        return jsonify({"error": "No audio file provided."}), 400
    
    extension_id = request.form.get('extension_id')
    if not extension_id:
        return jsonify({"error": "Extension ID is required."}), 400

    file = request.files['audio_file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    try:
        response = upload_greeting_to_extension(extension_id, file)
        return jsonify({"success": True, "message": "Uploaded successfully!", "data": response})
    
    except HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            retry_after = e.response.headers.get('Retry-After', 60)
            return jsonify({"error": "Rate limit exceeded.", "retry_after": int(retry_after)}), 429
        return jsonify({"error": str(e)}), e.response.status_code if e.response else 500
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@greetings_uploader_bp.route('/api/greetings_uploader/directory', methods=['POST'])
@require_rc_token
@track_usage('Greetings - Update Directory Visibility')
def api_set_directory_visibility():
    extension_id = request.form.get('extension_id')
    action = request.form.get('action') # 'show' or 'hide'
    
    if not extension_id or not action:
        return jsonify({"error": "Extension ID and action are required."}), 400
        
    # 'hidden' is True if we want to hide it, False if we want to show it
    is_hidden = True if action == 'hide' else False
    
    try:
        response = set_directory_visibility(extension_id, is_hidden)
        return jsonify({"success": True, "message": "Visibility updated!", "data": response})
        
    except HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            retry_after = e.response.headers.get('Retry-After', 60)
            return jsonify({"error": "Rate limit exceeded.", "retry_after": int(retry_after)}), 429
        return jsonify({"error": str(e)}), e.response.status_code if e.response else 500
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
