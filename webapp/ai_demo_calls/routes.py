import uuid
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from . import utils

ai_demo_calls_bp = Blueprint(
    'ai_demo_calls_bp', __name__,
    url_prefix='/api/ai_demo_calls'
)

@ai_demo_calls_bp.route('/generate-script', methods=['POST'])
@require_rc_token
def generate_demo_script():
    """Takes a scenario description and returns a Gemini-generated script."""
    data = request.get_json()
    scenario = data.get('scenario')
    voice_prompt = data.get('voice_prompt', 'Australian English')
    
    if not scenario:
        return jsonify({"error": "A scenario description is required."}), 400
        
    try:
        script_json = utils.generate_script_with_gemini(scenario, voice_prompt)
        return jsonify({"status": "success", "script": script_json})
    except Exception as e:
        print(f"Error generating script: {e}")
        return jsonify({"error": str(e)}), 500

@ai_demo_calls_bp.route('/generate-audio', methods=['POST'])
@require_rc_token
def generate_demo_audio():
    """Takes a generated JSON script and synthesizes the individual audio files."""
    data = request.get_json()
    script = data.get('script')
    template_id = data.get('template_id')
    voice_prompt = data.get('voice_prompt', 'Australian English')
    
    if not template_id:
        template_id = f"demo_{uuid.uuid4().hex[:8]}"
    
    if not script or not isinstance(script, list):
        return jsonify({"error": "A valid script array is required."}), 400
        
    try:
        audio_files = utils.generate_audio_for_script(script, template_id, voice_prompt)
        return jsonify({
            "status": "success", 
            "template_id": template_id,
            "files": audio_files
        })
    except Exception as e:
        print(f"Error generating audio: {e}")
        return jsonify({"error": "Failed to generate audio."}), 500

@ai_demo_calls_bp.route('/sip-provision', methods=['POST'])
@require_rc_token
def provision_sip():
    """Fetches WebRTC SIP credentials from RingCentral."""
    try:
        sip_data = utils.generate_sip_credentials()
        return jsonify({"status": "success", "sip_data": sip_data})
    except Exception as e:
        print(f"Error provisioning SIP: {e}")
        return jsonify({"error": "Failed to provision SIP credentials."}), 500
