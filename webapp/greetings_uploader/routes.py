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
    
    # Use getlist to retrieve all checked boxes from the FormData
    extension_ids = request.form.getlist('extension_ids')
    if not extension_ids:
        return jsonify({"error": "At least one Extension must be selected."}), 400

    file = request.files['audio_file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    results = {"successes": [], "failures": []}

    for ext_id in extension_ids:
        try:
            # CRITICAL: Reset the file stream pointer to the beginning before each upload
            # Otherwise, the second extension will receive an empty 0-byte file.
            file.seek(0) 
            response = upload_greeting_to_extension(ext_id, file)
            results["successes"].append({"id": ext_id, "status": "Success"})
        except Exception as e:
            results["failures"].append({"id": ext_id, "error": str(e)})

    # Determine overall status to return to the frontend
    if len(results["failures"]) == 0:
        return jsonify({"success": True, "message": f"Successfully uploaded to all {len(extension_ids)} extensions!"})
    elif len(results["successes"]) == 0:
        return jsonify({"error": f"Failed to upload to any extensions. Check the logs."}), 500
    else:
        return jsonify({"success": True, "message": f"Partial success: Uploaded to {len(results['successes'])}, failed on {len(results['failures'])}."})
