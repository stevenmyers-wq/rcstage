import io
from flask import Blueprint, jsonify, request, Response, send_file
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp.rc_api import rc_api_call
from webapp import task_control
from . import utils

message_management_bp = Blueprint(
    'message_management_bp', __name__,
    url_prefix='/api/message_management'
)
message_management_bp.add_url_rule('/xlsx_upload/cancel', 'xlsx_upload_cancel', task_control.cancel_view, methods=['POST'])

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
        transcribe = data.get('transcribe', False)

        if not ext_ids:
            return jsonify({'error': 'No endpoints selected for export'}), 400

        zip_buffer = utils.bulk_export_greetings(ext_ids, task_id, ignore_defaults, transcribe=transcribe)

        # Stream the archive as a chunked response instead of send_file. Cloud Run
        # rejects a *buffered* response body over 32 MiB with a 500 ("response size
        # too large"), which is what large export batches were hitting. A streamed
        # response (no Content-Length -> Transfer-Encoding: chunked) is exempt from
        # that limit, so the whole batch ZIP can be returned regardless of size.
        def _stream_zip(buf):
            while True:
                chunk = buf.read(65536)
                if not chunk:
                    break
                yield chunk

        return Response(
            _stream_zip(zip_buffer),
            mimetype='application/zip',
            headers={
                'Content-Disposition': 'attachment; filename="RingCentral_Audio_Export.zip"'
            },
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

@message_management_bp.route('/xlsx_template', methods=['GET'])
@require_rc_token
def xlsx_template():
    """Download a starter spreadsheet for the bulk XLSX upload workflow."""
    try:
        buffer = utils.generate_upload_template()
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Bulk_Greeting_Upload_Template.xlsx'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/xlsx_template_selected', methods=['POST'])
@require_rc_token
@track_usage('Message Management - Download Selected Template')
def xlsx_template_selected():
    """Download a spreadsheet pre-populated with every possible greeting slot for
    the endpoints selected in the table, ready for the bulk XLSX upload workflow."""
    try:
        data = request.get_json(silent=True) or {}
        ext_ids = data.get('ext_ids', [])
        if not ext_ids:
            return jsonify({'error': 'No endpoints selected'}), 400

        buffer = utils.generate_selected_template(ext_ids)
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Selected_Greeting_Upload_Template.xlsx'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/xlsx_upload', methods=['POST'])
@require_rc_token
@track_usage('Message Management - XLSX Bulk Upload')
def xlsx_upload():
    """Apply greetings in bulk from a spreadsheet + a public Google Drive folder."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Missing spreadsheet file'}), 400

        file_obj = request.files['file']
        drive_url = (request.form.get('drive_url') or '').strip()
        task_id = request.form.get('task_id')
        drive_token = (request.form.get('drive_token') or '').strip() or None

        # The client processes the sheet in chunks, refreshing the Drive token
        # between each, so no single request outlives the ~1h OAuth token.
        def _as_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        cursor = _as_int(request.form.get('cursor'), 0)
        raw_chunk = request.form.get('chunk_size')
        chunk_size = _as_int(raw_chunk, None) if raw_chunk not in (None, '') else None

        # The Drive folder link is optional: a sheet whose rows all use the TTS
        # column needs no Drive access. When a row does name a Drive file,
        # xlsx_bulk_upload raises a clear error if the link is missing.

        summary = utils.xlsx_bulk_upload(
            file_obj, drive_url, task_id, access_token=drive_token,
            cursor=cursor, chunk_size=chunk_size,
            sheet_name=(request.form.get('sheet_name') or None),
        )
        return jsonify({'success': True, **summary})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@message_management_bp.route('/xlsx_upload/status', methods=['GET'])
@require_rc_token
def xlsx_upload_status():
    """Poll endpoint for real-time bulk-upload progress and per-row log lines."""
    task_id = request.args.get('task_id')
    default = {'current': 0, 'total': 1, 'logs': []}
    if not task_id:
        return jsonify(default)
    return jsonify(utils.xlsx_upload_progress_store.get(task_id, default))