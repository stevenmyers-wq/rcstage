from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from .utils import get_message_extensions, upload_greeting_to_extension

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
        return jsonify({"success": True, "message": "Greeting uploaded successfully!", "data": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
