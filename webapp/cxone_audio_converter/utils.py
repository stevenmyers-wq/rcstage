import os
import io
import wave
import time
import tempfile
import subprocess
import requests
import urllib3
import pandas as pd

# Suppress the InsecureRequestWarning — same pattern used by cxone_script_analyzer
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# NOTE (Jun 2026): google-genai bumped from 0.3.0 → >=1.11.0. TTS calls here borrow get_gemini_client()
# from ai_demo_calls. If audio conversion breaks, contact Riyaz Mohammed before changing SDK usage.


def _run_ffmpeg(args, label="ffmpeg"):
    """Run an ffmpeg command. Raises RuntimeError with stderr on failure."""
    result = subprocess.run(
        ['ffmpeg', '-y'] + args,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode}):\n{result.stderr[-600:]}"
        )
    return result


def convert_to_ulaw(input_file_path):
    """
    Converts an audio file to CXone standard format (8kHz, Mono, u-law WAV).

    Single-pass ffmpeg pipeline:
      - Highpass at 100Hz  : removes low-frequency rumble
      - EBU R128 loudnorm  : normalises to -16 LUFS (telephone standard)
      - Resample 8kHz mono : SWR resampler for correct anti-aliasing
      - pcm_mulaw encode   : u-law encoding
      - map_metadata -1    : suppresses LIST/INFO metadata chunk from output
    """
    temp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    temp_out.close()

    _run_ffmpeg([
        '-i', input_file_path,
        '-af', ','.join([
            'highpass=f=100',
            'loudnorm=I=-16:TP=-1.5:LRA=11',
        ]),
        '-ar', '8000',
        '-ac', '1',
        '-acodec', 'pcm_mulaw',
        '-map_metadata', '-1',
        '-fflags', '+bitexact',
        temp_out.name,
    ], label="Audio conversion")

    return temp_out.name


def generate_tts_and_convert(text, voice_name='Kore', accent='Australian English'):
    """
    Generates TTS audio from text using Gemini TTS, then converts to CXone
    format (8kHz, Mono, u-law WAV) via a single ffmpeg pass that also:

      - Removes leading/trailing silence (Gemini adds ~300-400ms before speech)
      - Applies 100Hz highpass to remove low-frequency model artefacts
      - Normalises to EBU R128 -16 LUFS (telephony standard)

    Returns path to converted u-law WAV. Caller must delete the file after use.
    """
    from webapp.ai_demo_calls.utils import get_gemini_client, VALID_VOICES, DEFAULT_AGENT_VOICE
    from google.genai import types

    if voice_name not in VALID_VOICES:
        print(f"Warning: '{voice_name}' is not a valid Gemini voice. Falling back to '{DEFAULT_AGENT_VOICE}'.")
        voice_name = DEFAULT_AGENT_VOICE

    tts_prompt = (
        f"Voice instruction: Speak with a clear, professional {accent} accent. "
        f"Do NOT include any breathing sounds, sighs, gasps, inhales, or audio "
        f"artefacts of any kind. Deliver the text smoothly and naturally at a "
        f"measured pace with clean pronunciation. "
        f"Text to speak: {text}"
    )

    client = get_gemini_client()
    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        temp_raw = None
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash-preview-tts',
                contents=tts_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name
                            )
                        )
                    )
                )
            )

            pcm_data = response.candidates[0].content.parts[0].inline_data.data

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm_data)

            temp_raw = tempfile.NamedTemporaryFile(delete=False, suffix='_24k.wav')
            temp_raw.write(wav_buffer.getvalue())
            temp_raw.close()

            temp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            temp_out.close()

            _run_ffmpeg([
                '-i', temp_raw.name,
                '-af', ','.join([
                    'silenceremove='
                        'start_periods=1:start_silence=0.03:start_threshold=-50dB'
                        ':stop_periods=-1:stop_silence=0.15:stop_threshold=-50dB',
                    'highpass=f=100',
                    'loudnorm=I=-16:TP=-1.5:LRA=11',
                ]),
                '-ar', '8000',
                '-ac', '1',
                '-acodec', 'pcm_mulaw',
                '-map_metadata', '-1',
                '-fflags', '+bitexact',
                temp_out.name,
            ], label=f"TTS conversion attempt {attempt}")

            if attempt > 1:
                print(f"TTS succeeded on attempt {attempt} for: '{text[:50]}'")

            return temp_out.name

        except Exception as e:
            last_error = e
            print(f"TTS attempt {attempt}/{max_attempts} failed for '{text[:50]}': {e}")
            if 'temp_out' in locals() and temp_out and os.path.exists(temp_out.name):
                try:
                    os.unlink(temp_out.name)
                except Exception:
                    pass
            if attempt < max_attempts:
                time.sleep(2 * attempt)

        finally:
            if temp_raw is not None:
                try:
                    if os.path.exists(temp_raw.name):
                        os.unlink(temp_raw.name)
                except Exception:
                    pass

    raise ValueError(f"TTS generation failed after {max_attempts} attempts: {last_error}")


def upload_to_cxone(base_uri, token, file_bytes, cx_filename, overwrite=False):
    """
    Uploads a WAV file to CXone using the Admin API UploadFile endpoint.

    cx_filename should already be the full CXone path, e.g. prompts\\welcome.wav
    Raises an exception with the full NICE response body on any failure.

    NICE file upload APIs expect a JSON body where the file is base64-encoded —
    the same pattern used by their calling-list upload and other file endpoints.
    """
    import base64

    endpoint = f"{base_uri}/incontactapi/services/v34.0/files"

    # Normalise to forward slashes — accepted by NICE and safer in JSON
    cx_path = cx_filename.replace('\\', '/')

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "fileName": cx_path,
        "file": base64.b64encode(file_bytes).decode("utf-8"),
        "overwrite": overwrite,
    }

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        verify=False
    )

    if response.status_code == 401:
        raise Exception(
            "CXone session expired or unauthorised. Please disconnect and reconnect "
            "via the Authentication tab."
        )

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]

        # FileExists is an expected, recoverable condition — signal it distinctly
        # so callers can offer the user an overwrite prompt rather than a hard error.
        if isinstance(detail, dict) and detail.get('error_description') == 'FileExists':
            return {'file_exists': True}

        raise Exception(
            f"{response.status_code} {response.reason} — NICE response: {detail}"
        )

    return response.json() if response.content else {}


def parse_generation_file(file_storage):
    """
    Parses an uploaded CSV or XLSX file into a list of row dicts.

    Required columns : filename, text
    Optional columns : voice   (Gemini voice name — overrides default per-row)
                       accent  (e.g. "Australian English" — overrides default per-row)

    Returns (rows, error_message). On success error_message is None.
    """
    fname = file_storage.filename.lower()

    try:
        if fname.endswith('.xlsx'):
            df = pd.read_excel(file_storage, engine='openpyxl')
        elif fname.endswith('.csv'):
            df = pd.read_csv(file_storage)
        else:
            return None, "Unsupported file type. Please upload a .csv or .xlsx file."
    except Exception as e:
        return None, f"Could not read file: {str(e)}"

    df.columns = [c.strip().lower() for c in df.columns]

    if 'filename' not in df.columns or 'text' not in df.columns:
        return None, "File must contain 'filename' and 'text' columns."

    df = df.dropna(subset=['filename', 'text'])
    df['filename'] = df['filename'].astype(str).str.strip()
    df['text']     = df['text'].astype(str).str.strip()
    df = df[(df['filename'] != '') & (df['text'] != '')]

    if df.empty:
        return None, "No valid rows found. Check that 'filename' and 'text' columns are populated."

    if len(df) > 50:
        return None, f"Maximum 50 rows per run. Your file has {len(df)} rows."

    def safe_str(val):
        if val is None:
            return ''
        s = str(val).strip()
        return '' if s.lower() == 'nan' else s

    rows = []
    for _, row in df.iterrows():
        rows.append({
            'filename': row['filename'],
            'text':     row['text'],
            'voice':    safe_str(row.get('voice', ''))  if 'voice'  in df.columns else '',
            'accent':   safe_str(row.get('accent', '')) if 'accent' in df.columns else '',
        })

    return rows, None