import json
import os
import wave
import io
import base64
import concurrent.futures
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
    
    CRITICAL INSTRUCTION: The characters MUST speak using {voice_prompt} vocabulary, slang, and phrasing.
    
    Output strictly as a JSON array of objects, with no markdown formatting.
    Each object must have:
    - "speaker": Either "Customer" or "Agent".
    - "text": The exact words they say.
    - "emotion": A natural language instruction for the voice actor (e.g., "Speak very quickly and sound furiously angry").
    """
    
    try:
        # CHANGED: Switched to flash for massive speed improvements
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
    buffer = io.BytesIO() # Create a virtual file in RAM
    
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)        # Mono
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(24000)    # Gemini outputs audio at 24kHz
        wf.writeframes(pcm_data)
    
    # Get the raw bytes from the RAM buffer
    wav_bytes = buffer.getvalue()
    
    # Encode to Base64 and create a Data URI
    b64_encoded = base64.b64encode(wav_bytes).decode('utf-8')
    return f"data:audio/wav;base64,{b64_encoded}"

def _process_single_turn(index, turn, voice_prompt, client):
    """Helper function to generate a single audio clip (used for parallel processing)."""
    speaker = turn.get('speaker', 'Customer')
    text = turn.get('text', '')
    emotion = turn.get('emotion', 'Speak normally.')
    
    voice_name = 'Aoede' if speaker.lower() == 'agent' else 'Puck'
    tts_prompt = f"Voice instruction: You MUST speak with a very strong and natural {voice_prompt} accent/style. Style instruction: {emotion}. \nText to speak: {text}"
    
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
        
        # Get raw PCM bytes
        audio_bytes = response.candidates[0].content.parts[0].inline_data.data
        
        # Convert directly to Base64 Data URI instead of saving to disk
        audio_data_uri = create_wave_base64(audio_bytes)
        
        return {
            "turn": index,
            "speaker": speaker,
            "text": text,
            "emotion": emotion,
            "audio_url": audio_data_uri  
        }
    except Exception as e:
        print(f"Failed to generate audio for turn {index}: {e}")
        return {
            "turn": index,
            "speaker": speaker,
            "text": text,
            "error": str(e)
        }

def generate_audio_for_script(script_array, template_id, voice_prompt):
    """Loops through the script and generates expressive audio Base64 strings concurrently."""
    client = get_gemini_client()
    generated_files = []
    
    # CHANGED: Added ThreadPoolExecutor for Parallel Processing
    # max_workers=10 means we process up to 10 audio requests at the exact same time
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all tasks to the executor
        futures = [
            executor.submit(_process_single_turn, index, turn, voice_prompt, client) 
            for index, turn in enumerate(script_array)
        ]
        
        # Gather the results as they finish
        for future in concurrent.futures.as_completed(futures):
            generated_files.append(future.result())
            
    # Because threads finish at random times, we must sort the final array back into the correct order
    generated_files.sort(key=lambda x: x["turn"])
            
    return generated_files

def generate_sip_credentials():
    """Calls the RingCentral API to generate temporary WebRTC credentials."""
    endpoint = "/restapi/v1.0/client-info/sip-provision"
    payload = {
        "sipInfo": [{
            "transport": "WSS"
        }]
    }
    try:
        response = rc_api_call(endpoint, method="POST", json=payload)
        return response
    except Exception as e:
        print(f"FATAL ERROR in generate_sip_credentials: {e}")
        raise ValueError(f"Failed to fetch SIP credentials: {str(e)}")
