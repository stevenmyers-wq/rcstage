import io
from flask import Blueprint, jsonify, request, Response, send_file
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
from . import utils

message_management_bp = Blueprint(
    'message_management_bp', __name__,
    url_prefix='/api/message_management'
)

@message_management_bp.route('/endpoints', methods=['GET'])
@require_rc_token
def get_endpoints():
    try:
        data = utils.fetch_target_endpoints()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/greetings/<ext_id>', methods=['GET'])
@require_rc_token
def list_greetings(ext_id):
    try:
        data = utils.fetch_custom_greetings(ext_id)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/media/<ext_id>/<greeting_id>', methods=['GET'])
@require_rc_token
@track_usage('Message Management - Stream Greeting')
def stream_greeting(ext_id, greeting_id):
    try:
        is_ivr = request.args.get('is_ivr', 'false') == 'true'
        is_custom = request.args.get('is_custom', 'true') == 'true'
        greeting_type = request.args.get('greeting_type', '')
        preset_uri = request.args.get('preset_uri', '')
        text_param = request.args.get('text', '')
        is_download = request.args.get('download', 'false') == 'true'

        if greeting_id == 'tts' and text_param:
            audio_buffer = utils.generate_tts_audio_bytes(text_param, voice_name="Kore")
            if is_download:
                return send_file(audio_buffer, mimetype="audio/wav", as_attachment=True, download_name=f"IVR_TTS_{ext_id}.wav")
            return Response(audio_buffer.read(), mimetype="audio/wav")
            
        if greeting_id == 'default' and is_ivr:
            audio_buffer = utils.generate_tts_audio_bytes("This IVR Menu does not have an audio prompt configured.", voice_name="Kore")
            if is_download:
                return send_file(audio_buffer, mimetype="audio/wav", as_attachment=True, download_name=f"IVR_Default_{ext_id}.wav")
            return Response(audio_buffer.read(), mimetype="audio/wav")

        content, mime_type = utils.download_greeting_audio(
            ext_id, greeting_id, is_ivr=is_ivr, is_custom=is_custom, 
            greeting_type=greeting_type, preset_uri=preset_uri
        )
        
        headers = {}
        if is_download:
            ext = 'mp3' if 'mpeg' in mime_type or 'mp3' in mime_type else 'wav'
            headers['Content-Disposition'] = f'attachment; filename="{greeting_type}_{ext_id}.{ext}"'
            
        return Response(content, mimetype=mime_type, headers=headers)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Message Management - Upload Greeting')
def upload_greeting():
    try:
        ext_id = request.form.get('ext_id')
        greeting_type = request.form.get('greeting_type')
        
        if 'file' not in request.files or not ext_id or not greeting_type:
            return jsonify({'error': 'Missing file, extension ID, or greeting type'}), 400
            
        file_obj = request.files['file']
        result = utils.upload_custom_greeting(ext_id, file_obj, greeting_type, f"Upload ({file_obj.filename})")
        
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/generate_tts_audio_only', methods=['POST'])
@require_rc_token
@track_usage('Message Management - Bulk AI Generation')
def generate_tts_audio_only():
    try:
        data = request.get_json()
        text = data.get('text')
        voice = data.get('voice', 'Kore')
        
        if not text:
            return jsonify({'error': 'Missing text'}), 400

        audio_buffer = utils.generate_tts_audio_bytes(text, voice)
        return send_file(
            audio_buffer,
            mimetype='audio/wav',
            as_attachment=True,
            download_name=f"ai_generated_{voice}.wav"
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/export', methods=['POST'])
@require_rc_token
@track_usage('Message Management - Bulk Export Archive')
def export_greetings():
    try:
        data = request.get_json()
        ext_ids = data.get('ext_ids', [])
        task_id = data.get('task_id')
        ignore_defaults = data.get('ignore_defaults', False)
        
        if not ext_ids:
            return jsonify({'error': 'No endpoints selected for export'}), 400
            
        zip_buffer = utils.bulk_export_greetings(ext_ids, task_id, ignore_defaults)
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name='RingCentral_Audio_Export.zip'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/export/status', methods=['GET'])
@require_rc_token
def export_status():
    """Poll endpoint to fetch real-time zip generation progress"""
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'current': 0, 'total': 1})
        
    progress = utils.export_progress_store.get(task_id, {'current': 0, 'total': 1})
    return jsonify(progress)