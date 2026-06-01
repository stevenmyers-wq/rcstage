import json
import os
import wave
import io
import base64
import time
import concurrent.futures
import requests
# NOTE (Jun 2026): google-genai bumped from 0.3.0 → >=1.11.0 for SpeechConfig.language_code TTS support.
# If Gemini calls break after a dependency update, contact Riyaz Mohammed before changing SDK usage here.
from google import genai
from google.genai import types
from webapp.rc_api import rc_api_call  # Pulling in your RC API wrapper

def get_gemini_client():
    """Lazy-loads the Gemini client so it doesn't crash on server startup."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment.")
    return genai.Client(api_key=api_key)

def generate_script_with_gemini(scenario, voice_prompt):
    """Uses Gemini to generate an emotionally tagged, turn-by-turn script."""
    client = get_gemini_client()
    
    prompt = f"""
    You are an expert contact center script writer. 
    Write a realistic phone conversation based on this scenario: {scenario}
    
    CRITICAL INSTRUCTION: The characters MUST speak in {voice_prompt}. Write all dialogue entirely in that language, using natural vocabulary and phrasing appropriate to that region. The tone MUST be highly professional, polite, and corporate. DO NOT use heavy regional slang or overly casual colloquialisms.

    
    Output strictly as a JSON array of objects, with no markdown formatting.
    Each object must have:
    - "speaker": Either "Customer" or "Agent".
    - "text": The exact words they say (written in {voice_prompt}).
    - "emotion": A natural language instruction for the voice actor in English (e.g., "Speak very quickly and sound furiously angry").
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Gemini script generation failed: {e}")
        raise ValueError("Failed to parse Gemini script output into JSON.")

def create_wave_base64(pcm_data):
    """Helper function to wrap raw PCM audio data into a playable in-memory .wav file and return as Base64."""
    buffer = io.BytesIO()
    
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)        # Mono
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(24000)    # Gemini outputs audio at 24kHz
        wf.writeframes(pcm_data)
    
    wav_bytes = buffer.getvalue()
    b64_encoded = base64.b64encode(wav_bytes).decode('utf-8')
    return f"data:audio/wav;base64,{b64_encoded}"

# Maps UI voice_prompt values to BCP-47 language codes for reliable TTS language selection
LANGUAGE_CODE_MAP = {
    'Australian English':       'en-AU',
    'American English':         'en-US',
    'British English':          'en-GB',
    'French (France)':          'fr-FR',
    'French (Canadian)':        'fr-CA',
    'Spanish (Latin American)': 'es-US',
    'German':                   'de-DE',
}

# Whitelist of valid Gemini TTS voice names to prevent injection via user input
VALID_VOICES = {
    'Achernar', 'Achird', 'Algenib', 'Algieba', 'Alnilam', 'Aoede', 'Autonoe',
    'Callirrhoe', 'Charon', 'Despina', 'Enceladus', 'Erinome', 'Fenrir', 'Gacrux',
    'Iapetus', 'Kore', 'Laomedeia', 'Leda', 'Orus', 'Puck', 'Pulcherrima',
    'Rasalgethi', 'Sadachbia', 'Sadaltager', 'Schedar', 'Sulafat', 'Umbriel',
    'Vindemiatrix', 'Zephyr', 'Zubenelgenubi'
}

DEFAULT_AGENT_VOICE = 'Kore'
DEFAULT_CUSTOMER_VOICE = 'Puck'

def _resolve_voice(voice_name, default):
    """Returns the requested voice if it's in the whitelist, otherwise returns the default."""
    if voice_name and voice_name in VALID_VOICES:
        return voice_name
    if voice_name:
        print(f"Warning: '{voice_name}' is not a recognised Gemini TTS voice. Falling back to '{default}'.")
    return default

def _process_single_turn(index, turn, voice_prompt, client, agent_voice, customer_voice, language_code='en-AU'):
    """Helper function to generate a single audio clip (used for parallel processing).

    Retries up to 3 times with a short delay to handle Gemini TTS occasionally
    returning text tokens instead of audio tokens (known preview-model behaviour).
    """
    speaker = turn.get('speaker', 'Customer')
    text = turn.get('text', '')
    emotion = turn.get('emotion', 'Speak normally.')

    # Use the caller-supplied voice names (already validated by generate_audio_for_script)
    voice_name = agent_voice if speaker.lower() == 'agent' else customer_voice

    # language_code sets the accent at the API level; the prompt reinforces it per-turn
    # so the model doesn't drift back to American English mid-conversation
    tts_prompt = f"Language and accent: {voice_prompt}. Voice acting instruction: {emotion}\nText to speak: {text}"

    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash-preview-tts',
                contents=tts_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        language_code=language_code,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name
                            )
                        )
                    )
                )
            )

            # This is the line that raises AttributeError when Gemini returns
            # text tokens instead of audio — treat it as a retryable failure
            audio_bytes = response.candidates[0].content.parts[0].inline_data.data

            audio_data_uri = create_wave_base64(audio_bytes)

            if attempt > 1:
                print(f"Turn {index} succeeded on attempt {attempt}.")

            return {
                "turn": index,
                "speaker": speaker,
                "text": text,
                "emotion": emotion,
                "audio_url": audio_data_uri
            }

        except Exception as e:
            last_error = e
            print(f"Turn {index} attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(2 * attempt)  # 2s before retry 2, 4s before retry 3

    # All attempts exhausted
    print(f"Turn {index} failed after {max_attempts} attempts. Giving up.")
    return {
        "turn": index,
        "speaker": speaker,
        "text": text,
        "error": str(last_error)
    }

def generate_audio_for_script(script_array, template_id, voice_prompt, agent_voice=None, customer_voice=None):
    """Loops through the script and generates expressive audio Base64 strings concurrently."""
    client = get_gemini_client()

    # Validate voice names against the whitelist before spawning threads
    resolved_agent_voice = _resolve_voice(agent_voice, DEFAULT_AGENT_VOICE)
    resolved_customer_voice = _resolve_voice(customer_voice, DEFAULT_CUSTOMER_VOICE)

    # Resolve language code from the UI voice_prompt value; fall back to en-AU
    language_code = LANGUAGE_CODE_MAP.get(voice_prompt, 'en-AU')

    print(f"Generating audio — Agent: {resolved_agent_voice}, Customer: {resolved_customer_voice}, Language: {language_code}")

    generated_files = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                _process_single_turn,
                index, turn, voice_prompt, client,
                resolved_agent_voice, resolved_customer_voice, language_code
            )
            for index, turn in enumerate(script_array)
        ]
        
        for future in concurrent.futures.as_completed(futures):
            generated_files.append(future.result())
            
    generated_files.sort(key=lambda x: x["turn"])
            
    return generated_files

def get_demo_account_token(region="AU"):
    """Exchanges a region-specific JWT for a RingCentral access token."""
    client_id = os.environ.get("DEMO_RC_CLIENT_ID")
    client_secret = os.environ.get("DEMO_RC_CLIENT_SECRET")
    
    if region == "UK":
        jwt = os.environ.get("DEMO_RC_JWT_UK")
    elif region == "US":
        jwt = os.environ.get("DEMO_RC_JWT_US")
    else:
        jwt = os.environ.get("DEMO_RC_JWT_AU")
        
    if not all([client_id, client_secret, jwt]):
        raise ValueError(f"Missing JWT credentials for region {region} in environment.")
        
    token_url = "https://platform.ringcentral.com/restapi/oauth/token"
    auth_str = base64.b64encode(f"{client_id}:{client_secret}".encode('utf-8')).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {auth_str}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt
    }
    
    response = requests.post(token_url, headers=headers, data=data)
    
    if not response.ok:
        error_data = response.json() if response.content else {}
        if error_data.get("error") in ["invalid_grant", "invalid_client"]:
            raise ValueError(f"The JWT for the {region} region has expired or is invalid. Please generate a new one.")
        response.raise_for_status()
        
    return response.json().get("access_token")

def generate_sip_credentials(region="AU"):
    """Calls the RingCentral API to generate temporary WebRTC credentials."""
    demo_token = get_demo_account_token(region)

    endpoint = "/restapi/v1.0/client-info/sip-provision"
    payload = {
        "sipInfo": [{
            "transport": "WSS"
        }]
    }
    try:
        response = rc_api_call(endpoint, method="POST", json=payload, token=demo_token)
        return response
    except Exception as e:
        print(f"FATAL ERROR in generate_sip_credentials: {e}")
        raise ValueError(f"Failed to fetch SIP credentials: {str(e)}")
