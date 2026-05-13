# CLAUDE.md — RCAU API Tools

This file is read automatically by Claude Code at the start of every session.
It provides all context needed to work on this project without re-explanation.

## What this project is

An internal Flask web app for RingCentral Australia engineering staff.
It provides a suite of tools that interact with the RingCentral API on behalf of customer accounts.
Access is restricted to @ringcentral.com Google accounts via SSO.
Deployed on Google Cloud Run, built via Cloud Build on push to GitHub.

Production URLs:
- AU: https://rcau-api-tools-396158962307.us-central1.run.app/
- UK: https://rcuk-api-tools-396158962307.europe-west2.run.app/

GCP Project: sr-1906369, Project Number: 396158962307

## Stack

- Python 3.11, Flask, Gunicorn
- Jinja2 templates, Tailwind CSS (CDN, no build step), vanilla JavaScript
- Google Cloud Firestore (usage tracking, app config)
- Docker, Google Cloud Run, Google Cloud Build, Artifact Registry

## How the app is structured

Modular Flask Blueprint architecture. Each tool is fully self-contained:
- `webapp/module_name/routes.py` — Flask routes (thin HTTP layer)
- `webapp/module_name/utils.py` — business logic and API calls (not always present)
- `webapp/templates/includes/module_name_tab.html` — UI partial
- `webapp/static/js/module_name.js` — frontend JS

Shared utilities:
- `webapp/rc_api.py` — `rc_api_call()` for all RC API calls
- `webapp/auth_utils.py` — `@require_rc_token` decorator
- `webapp/usage_tracking.py` — `@track_usage()` decorator, logs to Firestore
- `webapp/firestore_utils.py` — reads app config from Firestore
- `webapp/static/js/app.js` — shared JS: `showMessage()`, `checkRcStatus()`, PKCE connect

New blueprints are registered in `webapp/__init__.py`.
New tabs are added to `{% set tabs = [...] %}` in `webapp/templates/index.html`.

## Authentication layers

1. Google SSO — all users. `core/routes.py`. Do not touch.
2. RingCentral PKCE OAuth — most tools. `session['rc_access_token']`. Use `@require_rc_token`.
3. JWT server-to-server — AI Demo Calls only. Token from env vars.
4. RingCX token exchange — RingCX Streaming only. Exchanges RC token for RingCX token.
   Session keys: `ringcx_access_token`, `ringcx_refresh_token`, `ringcx_account_id`.
   Expires every 5 mins, auto-refreshed by frontend every 4 mins.
5. Agent Form (/agent-form/) — intentionally no auth. Public route for iframe embedding.
   Only needs `dialog_id` URL param. Dialog IDs are unguessable UUIDs.

In development mode (`FLASK_ENV=development`), Google SSO is bypassed.
Auto-session: `authenticated=True`, `user_email=developer@local.test`, `is_admin=True`.

## Non-negotiable patterns

### Decorator order
```python
@blueprint.route('/endpoint', methods=['POST'])  # always first
@require_rc_token                                 # always second
@track_usage('Tool Name')                         # always third
def your_function():
```

### RC API calls
```python
from webapp.rc_api import rc_api_call
data = rc_api_call("/restapi/v1.0/account/~/sites")
# NEVER pass token manually — TypeError
```

### Blueprint naming
```python
your_tool_bp = Blueprint('your_tool_bp', __name__, url_prefix='/api/your_tool_name')
```

### Tab registration
```python
('tab_id', 'Display Name', 'Short description'),
```
tab_id must match folder name and {% elif current_tab == 'tab_id' %} include block.

### Frontend fetch pattern
```javascript
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('your-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
            const response = await fetch('/api/your_tool/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'value' })
            });
            const data = await response.json();
            if (response.ok) { showMessage('Done.'); }
            else { showMessage(data.error || 'Error.', true); }
        } catch (err) {
            showMessage('Network error.', true);
        } finally { btn.disabled = false; }
    });
});
```

## Files that should rarely or never be touched
- `webapp/core/routes.py`
- `webapp/auth/routes.py`
- `webapp/__init__.py` — only add blueprint registrations
- `webapp/rc_api.py`
- `webapp/auth_utils.py`

## Environment variables
Always: FLASK_SECRET_KEY, FLASK_ENV, GOOGLE_CLIENT_ID, RC_REDIRECT_URI, RC_SERVER_URL, RC_SCOPE, ADMIN_EMAILS
RingCX + Agent Form: RCAU_WEBHOOK_SECRET, GCP_PROJECT_NUMBER (default: 396158962307), GEMINI_API_KEY
AI Demo Calls: DEMO_RC_JWT_AU, DEMO_RC_JWT_UK, DEMO_RC_JWT_US, DEMO_RC_CLIENT_ID, DEMO_RC_CLIENT_SECRET
Analytics: SM_CLIENT_ID, SM_CLIENT_SECRET

## Deployment

### Flask app
Push to GitHub → Cloud Build → builds Docker image → deploys to Cloud Run (AU + UK).
Existing trigger has `grpc_streaming/**` in ignored files filter.

### gRPC streaming service (grpc_streaming/)
Separate Cloud Build trigger:
- Name: rcau-grpc-streaming
- Included files: grpc_streaming/**
- Config: grpc_streaming/cloudbuild.yaml
- Deploys: rcau-rcx-grpc-streaming in us-central1
Env vars set manually in Cloud Run console (not in code).

## gRPC Streaming Service (grpc_streaming/)

Separate Cloud Run service receiving live audio from RingCX via gRPC.

### How it works
1. RingCX Workflow Studio Start Streaming node connects to gRPC service
2. servicer.py handles StreamEvents:
   - DialogInit → POSTs `dialog_start` to Flask `/api/audio_streaming/dialog-event`
   - SegmentStart → creates SegmentTranscriber (transcription.py) per participant
   - SegmentMedia → feeds audio to Google STT via transcription.py
   - SegmentStop → stops transcriber
   - Stream closes → POSTs `dialog_end` to Flask
3. Each transcript result → POSTs to Flask `/api/audio_streaming/transcript-event`
4. Flask pushes via SSE to browser subscribers

### Proto stubs
Pre-compiled in grpc_streaming/generated/. DO NOT REGENERATE.
streaming_pb2_grpc.py line 7 has manual fix: `from generated import streaming_pb2`
Regenerating overwrites this and breaks the server.

### RingCX Workflow Studio URL
grpc://rcau-rcx-grpc-streaming-396158962307.us-central1.run.app:443
MUST use grpc:// scheme. https:// causes STREAMING SETUP FAILED.
Credentials: Basic Auth, any username/password (server accepts all).

## audio_streaming blueprint (webapp/audio_streaming/)

routes.py endpoints:
- POST /api/audio_streaming/ringcx-token — RingCX token exchange (requires @require_rc_token)
- POST /api/audio_streaming/ringcx-refresh — token refresh (no auth, uses session refresh token)
- GET /api/audio_streaming/accounts — fetch RingCX sub-accounts
- GET /api/audio_streaming/ringcx-status — session connection state
- POST /api/audio_streaming/ringcx-disconnect — clear session tokens
- GET /api/audio_streaming/grpc-service-url — returns Workflow Studio URL from GCP_PROJECT_NUMBER
- POST /api/audio_streaming/dialog-event — receives dialog_start/dialog_end from gRPC service
- GET /api/audio_streaming/active-dialogs — returns in-memory list of active calls (no auth)
- POST /api/audio_streaming/transcript-event — receives transcript lines from gRPC service
- GET /api/audio_streaming/transcript-stream/<dialog_id> — SSE endpoint for live transcripts

In-memory stores (reset on container restart — intentional for PoC):
- _active_dialogs: {dialog_id: {ani, dnis, started_at}}
- _transcript_subscribers: {dialog_id: [Queue, ...]}

## agent_form blueprint (webapp/agent_form/)

routes.py endpoints:
- GET /agent-form/ — renders standalone agent_form.html (no RCAU chrome)
  Params: dialog_id (required), ani (optional, pre-populates phone field)
- POST /agent-form/suggest — takes transcript text, calls Gemini, returns field suggestions JSON

agent_form.html — standalone minimal page:
- Left panel: live transcript (connects SSE automatically from dialog_id URL param)
- Right panel: personal injury triage form with AI suggestion pills
- Every 3 final transcript lines → calls /agent-form/suggest → renders accept/dismiss pills
- No RCAU session required — completely public route

agent_form_tab.html + agent_form_tab.js — RCAU debug tab:
- Polls /api/audio_streaming/active-dialogs every 5s
- Shows active call dropdown (shared with streaming tab via same API)
- Load Form button → loads /agent-form/ into iframe + starts transcript mirror SSE
- Shows iframe URL with copy button for agent script configuration

Form fields (defined in agent_form/routes.py FORM_FIELDS):
- Caller Details: Full Name, Phone (pre-filled from ANI), Best Contact Time
- Incident Details: Type, Date, Location, Description
- Medical: Seen Doctor, Injury Nature, Still Treating
- Viability: Other Party Fault, Prior Claim, Lead Quality

## Working style for this project
- Explain what you are going to do and why before making any changes
- Show me the diff and wait for approval before writing to any file
- When making RC API calls in new code, check rc_api.py first
- If something touches core/, auth/, or __init__.py, flag it and explain why
- After completing a change, tell me what to test and how
