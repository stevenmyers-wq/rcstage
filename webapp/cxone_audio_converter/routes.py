from flask import Blueprint, request, send_file, jsonify, session
import os
import base64
import tempfile
import zipfile
import io
import concurrent.futures
from webapp.auth_utils import is_authenticated
from webapp.usage_tracking import track_usage
from .utils import convert_to_ulaw, generate_tts_and_convert, parse_generation_file, upload_to_cxone

cxone_audio_converter_bp = Blueprint('cxone_audio_converter_bp', __name__, url_prefix='/api/cxone_audio_converter')


@cxone_audio_converter_bp.route('/convert', methods=['POST'])
@track_usage('CXone Audio Converter')
def convert_audio():
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    files = request.files.getlist('audio')
    if not files or files[0].filename == '':
        return jsonify({"error": "No selected files."}), 400

    temp_inputs = []
    converted_files = []

    try:
        for file in files:
            if file.filename:
                temp_input = tempfile.NamedTemporaryFile(delete=False)
                file.save(temp_input.name)
                temp_input.close()
                temp_inputs.append(temp_input.name)

                converted_file_path = convert_to_ulaw(temp_input.name)
                original_name = os.path.splitext(file.filename)[0]
                converted_files.append((converted_file_path, f"cxone_{original_name}.wav"))

        if len(converted_files) == 1:
            file_path, file_name = converted_files[0]
            with open(file_path, 'rb') as f:
                file_data = f.read()
            memory_file = io.BytesIO(file_data)
            return send_file(
                memory_file,
                as_attachment=True,
                download_name=file_name,
                mimetype="audio/wav"
            )
        else:
            memory_zip = io.BytesIO()
            with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path, file_name in converted_files:
                    zipf.write(file_path, arcname=file_name)
            memory_zip.seek(0)
            return send_file(
                memory_zip,
                as_attachment=True,
                download_name="cxone_converted_audio.zip",
                mimetype="application/zip"
            )

    except Exception as e:
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500

    finally:
        for temp_in in temp_inputs:
            if os.path.exists(temp_in):
                try:
                    os.unlink(temp_in)
                except Exception:
                    pass
        for converted_path, _ in converted_files:
            if os.path.exists(converted_path):
                try:
                    os.unlink(converted_path)
                except Exception:
                    pass


@cxone_audio_converter_bp.route('/generate', methods=['POST'])
@track_usage('CXone Audio Generator')
def generate_audio():
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    file = request.files.get('generation_file')
    if not file or file.filename == '':
        return jsonify({"error": "No file uploaded."}), 400

    default_voice  = request.form.get('voice',  'Kore')
    default_accent = request.form.get('accent', 'Australian English')

    rows, parse_error = parse_generation_file(file)
    if parse_error:
        return jsonify({"error": parse_error}), 400

    converted_files = []
    errors = []

    def process_row(row):
        voice  = row['voice']  if row['voice']  else default_voice
        accent = row['accent'] if row['accent'] else default_accent
        output_name = row['filename']
        if not output_name.lower().endswith('.wav'):
            output_name += '.wav'
        output_name = f"cxone_{output_name}"
        try:
            converted_path = generate_tts_and_convert(row['text'], voice, accent)
            return (converted_path, output_name, None)
        except Exception as e:
            return (None, output_name, str(e))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(process_row, row) for row in rows]
            for future in concurrent.futures.as_completed(futures):
                path, name, error = future.result()
                if error:
                    errors.append(f"{name}: {error}")
                else:
                    converted_files.append((path, name))

        if not converted_files:
            return jsonify({"error": f"All rows failed to generate. Details: {'; '.join(errors)}"}), 500

        memory_zip = io.BytesIO()
        with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path, file_name in converted_files:
                zipf.write(file_path, arcname=file_name)
            if errors:
                zipf.writestr("generation_errors.txt", "\n".join(errors))

        memory_zip.seek(0)
        return send_file(
            memory_zip,
            as_attachment=True,
            download_name="cxone_generated_audio.zip",
            mimetype="application/zip"
        )

    except Exception as e:
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500

    finally:
        for converted_path, _ in converted_files:
            if converted_path and os.path.exists(converted_path):
                try:
                    os.unlink(converted_path)
                except Exception:
                    pass


@cxone_audio_converter_bp.route('/generate_and_upload', methods=['POST'])
@track_usage('CXone Audio Generate and Upload')
def generate_and_upload():
    """
    Generates TTS audio for each CSV row and uploads directly to CXone —
    no ZIP is created and nothing is downloaded to the browser.

    Returns JSON:
      {
        "results": [
          {"filename": "welcome_greeting.wav", "cx_path": "prompts/welcome_greeting.wav",
           "success": true, "error": null},
          ...
        ],
        "success_count": N,
        "total": N
      }
    """
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    if not session.get('cxone_token') or not session.get('cxone_base_uri'):
        return jsonify({"error": "CXone authentication required. Please connect via the Authentication tab."}), 401

    file = request.files.get('generation_file')
    if not file or file.filename == '':
        return jsonify({"error": "No file uploaded."}), 400

    default_voice  = request.form.get('voice',  'Kore')
    default_accent = request.form.get('accent', 'Australian English')

    rows, parse_error = parse_generation_file(file)
    if parse_error:
        return jsonify({"error": parse_error}), 400

    # Track generated temp paths for cleanup
    temp_paths = []
    results    = []

    def generate_row(row):
        """Generate TTS for one row. Returns (temp_path, output_name, cx_path, error)."""
        voice  = row['voice']  if row['voice']  else default_voice
        accent = row['accent'] if row['accent'] else default_accent

        output_name = row['filename']
        if not output_name.lower().endswith('.wav'):
            output_name += '.wav'

        cx_path = f"prompts/{output_name}"

        try:
            path = generate_tts_and_convert(row['text'], voice, accent)
            return (path, output_name, cx_path, None)
        except Exception as e:
            return (None, output_name, cx_path, str(e))

    try:
        # Generate all files concurrently
        generated = []  # (temp_path, output_name, cx_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(generate_row, row) for row in rows]
            for future in concurrent.futures.as_completed(futures):
                path, name, cx_path, error = future.result()
                if error:
                    results.append({
                        "filename": name,
                        "cx_path":  cx_path,
                        "success":  False,
                        "error":    f"Generation failed: {error}",
                    })
                else:
                    generated.append((path, name, cx_path))
                    temp_paths.append(path)

        # Upload each generated file to CXone sequentially
        for path, name, cx_path in generated:
            try:
                with open(path, 'rb') as f:
                    file_bytes = f.read()

                result = upload_to_cxone(
                    session['cxone_base_uri'],
                    session['cxone_token'],
                    file_bytes,
                    cx_path,
                    overwrite=False
                )

                if result.get('file_exists'):
                    # File exists in CXone — return bytes so frontend can offer overwrite
                    results.append({
                        "filename":      name,
                        "cx_path":       cx_path,
                        "success":       False,
                        "exists":        True,
                        "error":         "File already exists in CXone",
                        "file_data_b64": base64.b64encode(file_bytes).decode(),
                    })
                else:
                    results.append({
                        "filename": name,
                        "cx_path":  cx_path,
                        "success":  True,
                        "exists":   False,
                        "error":    None,
                    })
            except Exception as e:
                results.append({
                    "filename": name,
                    "cx_path":  cx_path,
                    "success":  False,
                    "exists":   False,
                    "error":    f"Upload failed: {str(e)}",
                })

        success_count = sum(1 for r in results if r['success'])
        return jsonify({
            "results":       results,
            "success_count": success_count,
            "total":         len(results),
        })

    except Exception as e:
        return jsonify({"error": f"Process failed: {str(e)}"}), 500

    finally:
        for path in temp_paths:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass


@cxone_audio_converter_bp.route('/upload', methods=['POST'])
@track_usage('CXone Audio Upload')
def upload_file():
    """
    Uploads a single WAV file to CXone prompts folder.
    Used by the post-download upload panel in both converter sections.
    """
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    if not session.get('cxone_token') or not session.get('cxone_base_uri'):
        return jsonify({"error": "CXone authentication required. Please connect via the Authentication tab."}), 401

    uploaded = request.files.get('file')
    filename  = request.form.get('filename', '').strip()

    if not uploaded or not filename:
        return jsonify({"error": "A WAV file and filename are required."}), 400

    base = os.path.basename(filename)
    if base.lower().startswith('cxone_'):
        base = base[6:]
    if not base.lower().endswith('.wav'):
        base += '.wav'

    cx_filename = f"prompts/{base}"

    try:
        file_bytes = uploaded.read()
        upload_to_cxone(
            session['cxone_base_uri'],
            session['cxone_token'],
            file_bytes,
            cx_filename,
            overwrite=False
        )
        return jsonify({"success": True, "cx_filename": cx_filename})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cxone_audio_converter_bp.route('/convert_and_upload', methods=['POST'])
@track_usage('CXone Audio Convert and Upload')
def convert_and_upload():
    """
    Converts uploaded audio files to CXone format and uploads each directly —
    no ZIP created, no browser download. Returns JSON results.
    """
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    if not session.get('cxone_token') or not session.get('cxone_base_uri'):
        return jsonify({"error": "CXone authentication required."}), 401

    files = request.files.getlist('audio')
    if not files or files[0].filename == '':
        return jsonify({"error": "No files selected."}), 400

    results    = []
    temp_paths = []

    try:
        for file in files:
            if not file.filename:
                continue

            temp_in = tempfile.NamedTemporaryFile(delete=False)
            file.save(temp_in.name)
            temp_in.close()
            temp_paths.append(temp_in.name)

            base_name = os.path.splitext(file.filename)[0]
            wav_name  = f"{base_name}.wav"
            cx_path   = f"prompts/{wav_name}"

            try:
                converted_path = convert_to_ulaw(temp_in.name)
                temp_paths.append(converted_path)

                with open(converted_path, 'rb') as f:
                    file_bytes = f.read()

                result = upload_to_cxone(
                    session['cxone_base_uri'],
                    session['cxone_token'],
                    file_bytes,
                    cx_path,
                    overwrite=False
                )

                if result.get('file_exists'):
                    results.append({
                        "filename": wav_name, "cx_path": cx_path,
                        "success": False, "exists": True,
                        "error": "File already exists in CXone",
                        "file_data_b64": base64.b64encode(file_bytes).decode(),
                    })
                else:
                    results.append({"filename": wav_name, "cx_path": cx_path, "success": True, "exists": False, "error": None})

            except Exception as e:
                results.append({"filename": wav_name, "cx_path": cx_path, "success": False, "exists": False, "error": str(e)})

        success_count = sum(1 for r in results if r['success'])
        return jsonify({"results": results, "success_count": success_count, "total": len(results)})

    except Exception as e:
        return jsonify({"error": f"Process failed: {str(e)}"}), 500

    finally:
        for p in temp_paths:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


@cxone_audio_converter_bp.route('/overwrite', methods=['POST'])
@track_usage('CXone Audio Overwrite')
def overwrite_files():
    """
    Re-uploads files that previously failed with FileExists, this time with
    overwrite=True.  Expects JSON body:
      { "files": [ {"cx_path": "prompts/...", "file_data_b64": "<base64>"}, ... ] }
    """
    if not is_authenticated():
        return jsonify({"error": "Authentication required."}), 401

    if not session.get('cxone_token') or not session.get('cxone_base_uri'):
        return jsonify({"error": "CXone authentication required."}), 401

    data  = request.get_json()
    files = data.get('files', []) if data else []
    if not files:
        return jsonify({"error": "No files provided."}), 400

    results = []
    for entry in files:
        cx_path = entry.get('cx_path', '')
        b64     = entry.get('file_data_b64', '')
        if not cx_path or not b64:
            results.append({"cx_path": cx_path, "success": False, "error": "Missing data"})
            continue

        try:
            file_bytes = base64.b64decode(b64)
            upload_to_cxone(
                session['cxone_base_uri'],
                session['cxone_token'],
                file_bytes,
                cx_path,
                overwrite=True
            )
            results.append({"cx_path": cx_path, "success": True, "error": None})
        except Exception as e:
            results.append({"cx_path": cx_path, "success": False, "error": str(e)})

    success_count = sum(1 for r in results if r['success'])
    return jsonify({"results": results, "success_count": success_count, "total": len(results)})


@cxone_audio_converter_bp.route('/validate_cxone', methods=['GET'])
def validate_cxone():
    """
    Validates the CXone session by making a lightweight live call to NICE.
    The local session token may still exist after it has expired on NICE's
    side, so checking session keys alone is not enough.
    Returns {"valid": true/false, "bu_name": "...", "reason": "..."}.
    """
    if not is_authenticated():
        return jsonify({"valid": False, "reason": "not_authenticated"})

    token    = session.get('cxone_token')
    base_uri = session.get('cxone_base_uri')
    bu_name  = session.get('cxone_bu_name', '')

    if not token or not base_uri:
        return jsonify({"valid": False, "reason": "not_connected"})

    import requests as req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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