from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from .utils import get_message_extensions, upload_greeting_to_extension
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
def api_upload_greeting():
    if 'audio_file' not in request.files:
        return jsonify({"error": "No audio file provided."}), 400
    
    # We now process one at a time so the frontend can track progress
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
        # Specifically catch Rate Limiting to inform the frontend loop
        if e.response is not None and e.response.status_code == 429:
            # RingCentral tells us exactly how many seconds to wait
            retry_after = e.response.headers.get('Retry-After', 60)
            return jsonify({"error": "Rate limit exceeded.", "retry_after": int(retry_after)}), 429
        
        return jsonify({"error": str(e)}), e.response.status_code if e.response else 500
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
