import os
import json
import requests as http_requests
from flask import Blueprint, jsonify, request, render_template, current_app
from webapp.usage_tracking import track_usage

agent_form_bp = Blueprint(
    'agent_form_bp', __name__,
    url_prefix='/agent-form'
)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# Personal injury triage form field definitions.
# Each field has an id, label, type, and optional options list.
FORM_FIELDS = [
    # Caller Details
    {'id': 'caller_name', 'label': 'Full Name', 'section': 'Caller Details', 'type': 'text'},
    {'id': 'caller_phone', 'label': 'Phone Number', 'section': 'Caller Details', 'type': 'text', 'from_ani': True},
    {'id': 'contact_time', 'label': 'Best Contact Time', 'section': 'Caller Details', 'type': 'text'},

    # Incident Details
    {'id': 'incident_type', 'label': 'Type of Incident', 'section': 'Incident Details', 'type': 'select',
     'options': ['Motor Vehicle Accident', 'Workplace Injury', 'Public Liability', 'Medical Negligence', 'Other']},
    {'id': 'incident_date', 'label': 'Date of Incident', 'section': 'Incident Details', 'type': 'text'},
    {'id': 'incident_location', 'label': 'Location / State', 'section': 'Incident Details', 'type': 'text'},
    {'id': 'incident_description', 'label': 'What Happened', 'section': 'Incident Details', 'type': 'textarea'},

    # Medical
    {'id': 'seen_doctor', 'label': 'Seen a Doctor?', 'section': 'Medical', 'type': 'select',
     'options': ['Yes', 'No', 'Not yet']},
    {'id': 'injury_nature', 'label': 'Nature of Injuries', 'section': 'Medical', 'type': 'textarea'},
    {'id': 'still_treating', 'label': 'Still Receiving Treatment?', 'section': 'Medical', 'type': 'select',
     'options': ['Yes', 'No']},

    # Viability
    {'id': 'other_party_fault', 'label': 'Another Party at Fault?', 'section': 'Viability', 'type': 'select',
     'options': ['Yes', 'No', 'Unsure']},
    {'id': 'prior_claim', 'label': 'Prior Claim for This Incident?', 'section': 'Viability', 'type': 'select',
     'options': ['Yes', 'No']},
    {'id': 'lead_quality', 'label': 'Lead Quality', 'section': 'Viability', 'type': 'select',
     'options': ['Hot', 'Warm', 'Cold']},
]


@agent_form_bp.route('/', methods=['GET'])
def agent_form_page():
    """
    Renders the standalone agent form page.
    Designed to be embedded as an iframe in RingCX agent scripts.
    No RCAU chrome — minimal page with just the form.
    dialog_id and ani come from URL query params (set by RingCX workflow variables).
    """
    dialog_id = request.args.get('dialog_id', '')
    ani = request.args.get('ani', '')
    return render_template(
        'agent_form.html',
        dialog_id=dialog_id,
        ani=ani,
        form_fields=FORM_FIELDS,
    )


@agent_form_bp.route('/suggest', methods=['POST'])
@track_usage('Agent Form - AI Suggest')
def suggest():
    """
    Takes the accumulated transcript and returns AI-suggested values
    for each form field. Called every 3 final transcript lines.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    transcript = data.get('transcript', '')
    if not transcript:
        return jsonify({'suggestions': {}})

    if not GEMINI_API_KEY:
        return jsonify({'error': 'Gemini API key not configured'}), 500

    # Build field list for the prompt
    field_descriptions = '\n'.join([
        f'- {f["id"]}: {f["label"]}' + (f' (options: {", ".join(f["options"])})' if 'options' in f else '')
        for f in FORM_FIELDS
        if not f.get('from_ani')  # Skip ANI field — populated from URL param
    ])

    prompt = f"""You are an AI assistant helping a personal injury law firm triage incoming calls.

Based on the following call transcript, extract information to fill in the triage form fields below.
Return a JSON object where each key is a field id and the value is your suggested answer.
Only include fields where you have reasonable confidence based on what was said.
For select fields, only suggest values from the provided options.
Do not guess — if unsure, omit the field.
Return ONLY the JSON object, no other text.

TRANSCRIPT:
{transcript}

FORM FIELDS:
{field_descriptions}

Return format example:
{{"caller_name": "John Smith", "incident_type": "Motor Vehicle Accident", "incident_date": "15 March 2026"}}"""

    try:
        response = http_requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}',
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.1}
            },
            timeout=15
        )
        response.raise_for_status()
        result = response.json()

        text = result['candidates'][0]['content']['parts'][0]['text']
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        suggestions = json.loads(text)
        return jsonify({'suggestions': suggestions})

    except Exception as e:
        current_app.logger.error(f'Gemini suggest error: {e}')
        return jsonify({'error': str(e)}), 500
