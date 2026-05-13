# RingCentral Australia Engineering API Tools (RCAU)

An internal web application providing a suite of tools for interacting with the RingCentral API. Designed for RingCentral support, administration, and engineering staff. Access is restricted to `@ringcentral.com` Google accounts.

**Production URLs:**
- AU: `https://rcau-api-tools-396158962307.us-central1.run.app/`
- UK: `https://rcuk-api-tools-396158962307.europe-west2.run.app/`

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Authentication Models](#authentication-models)
- [Project Structure](#project-structure)
- [Current Tools](#current-tools)
- [Developer Guide: Adding a New Tool](#developer-guide-adding-a-new-tool)
- [Key Development Patterns](#key-development-patterns)
- [Frontend Patterns](#frontend-patterns)
- [Environment Variables Reference](#environment-variables-reference)
- [Local Development Setup](#local-development-setup)
- [Deployment](#deployment)
- [RingCX gRPC Streaming Service](#ringcx-grpc-streaming-service)

---

## Architecture Overview

The application is a **modular Flask Blueprint** app. Each tool is a fully self-contained module with its own backend routes, business logic, HTML template partial, and JavaScript file. Modules share a small set of common utilities but are otherwise independent.

**Tech stack:**
- **Backend:** Python 3.11, Flask, Gunicorn
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN — no build step), vanilla JavaScript
- **Database:** Google Cloud Firestore (usage tracking and config)
- **Deployment:** Docker → Google Artifact Registry → Google Cloud Run, triggered by Cloud Build on push

---

## Authentication Models

### Layer 1 — Website Auth (Google SSO)
All users must sign in with a `@ringcentral.com` Google account. Handled by `webapp/core/routes.py`. Do not modify.

### Layer 2 — RingCentral PKCE OAuth (most tools)
Most tools act on behalf of a specific RingCentral customer account via PKCE flow.
Session keys: `session['rc_access_token']`, `session['rc_client_id']`
Protect routes with `@require_rc_token`.

Callback URL: `https://rcau-api-tools-396158962307.us-central1.run.app/auth/callback`
Local: `http://localhost:8080/auth/callback`

### Layer 3 — JWT Server-to-Server (AI Demo Calls only)
Static RingCentral JWTs from env vars. Does not use PKCE.

### Layer 4 — RingCX Token Exchange (RingCX Streaming)
Exchanges an existing RC PKCE token for a RingCX-specific token. Requires Layer 2 first.
Session keys: `session['ringcx_access_token']`, `session['ringcx_refresh_token']`, `session['ringcx_account_id']`
Token expires every 5 minutes — auto-refreshed by frontend every 4 minutes.

### Agent Form (no auth)
The `/agent-form` route is intentionally public — it's embedded as an iframe in RingCX agent scripts where agents have no RCAU session. It only needs a `dialog_id` URL parameter. Dialog IDs are long unguessable UUIDs.

---

## Project Structure

```
RCAU/
├── main.py
├── requirements.txt
├── dockerfile
├── cloudbuild.yaml                  # Deploys Flask to AU + UK
│
├── grpc_streaming/                  # Standalone gRPC streaming Cloud Run service
│   ├── main.py                      # gRPC server entry point
│   ├── servicer.py                  # StreamEvent handler + dialog/transcript webhooks
│   ├── transcription.py             # Google STT streaming per participant
│   ├── requirements.txt
│   ├── dockerfile
│   ├── cloudbuild.yaml              # Deploys to rcau-rcx-grpc-streaming
│   ├── proto/streaming.proto        # RingCX gRPC protocol definition (v1beta2)
│   └── generated/                   # Pre-compiled proto stubs (do not regenerate)
│       ├── __init__.py
│       ├── streaming_pb2.py
│       └── streaming_pb2_grpc.py    # Line 7: must read `from generated import streaming_pb2`
│
└── webapp/
    ├── __init__.py                  # App factory — register all blueprints here
    ├── rc_api.py
    ├── auth_utils.py
    ├── usage_tracking.py
    ├── firestore_utils.py
    │
    ├── audio_streaming/             # RingCX streaming tab
    │   ├── __init__.py
    │   ├── routes.py                # Auth, SSE, webhook, active dialogs, gRPC URL
    │   └── utils.py                 # RingCX token exchange/refresh/accounts
    │
    ├── agent_form/                  # Agent form tab + standalone iframe page
    │   ├── __init__.py
    │   └── routes.py                # /agent-form/ page + /agent-form/suggest AI endpoint
    │
    ├── static/js/
    │   ├── app.js
    │   ├── audio_streaming.js       # Streaming tab JS
    │   ├── agent_form_tab.js        # Agent form RCAU tab JS
    │   └── ...
    │
    └── templates/
        ├── index.html
        ├── agent_form.html          # Standalone iframe page (no RCAU chrome)
        └── includes/
            ├── audio_streaming_tab.html
            ├── agent_form_tab.html
            └── ...
```

---

## Current Tools

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `auth_rex` | REX Authentication | Layer 1 | PKCE OAuth connection |
| `auth_cxone` | RCCC Authentication | Layer 1 | CXone authentication |
| `sip_fetcher` | SIP Credentials | Layer 2 | Fetch SIP credentials |
| `device_swap` | Device Swap | Layer 2 | Swap DLs/Extensions |
| `renamer` | Extension Renamer | Layer 2 | Bulk edit extension names |
| `bulk_opening` | Bulk Opening Hours | Layer 2 | Mass Site/Queue hours config |
| `call_flow` | Call Flow Visualiser | Layer 2 | Visual routing path |
| `personal_address_book` | Multi User Address Book | Layer 2 | Multi-user address book |
| `live_events` | Live Events | Layer 2 | Real-time subscription listener |
| `custom_rules` | Custom Rules | Layer 2 | Bulk answering rules via CSV |
| `notifications` | Notifications | Layer 2 | Audit/update notification prefs |
| `greetings_uploader` | Greetings Uploader | Layer 2 | Upload greetings |
| `ringex_uat` | RingEX UAT | Layer 2 | Generate UAT scripts |
| `ai_demo_calls` | AI Demo Calls | Layer 3 | Generate demo calls |
| `analytics` | Business Analytics | Layer 2 | Call performance reports |
| `presence` | BLF & Presence | Layer 2 | Audit BLF monitored lines |
| `account_discovery` | Account Discovery | Layer 2 | Pre-engagement account analysis |
| `cxone_script_analyzer` | CXone Script Analyzer | Layer 2 | Changelogs & as-builts |
| `cxone_audio_converter` | CXone Audio Converter | Layer 2 | Convert audio files |
| `port_mapping` | Port Mapping | Layer 2 | Map phone numbers from LOA/BRD |
| `audio_streaming` | RingCX Streaming | Layer 4 | Live call transcript monitor |
| `agent_form` | Agent Form | Layer 1 (tab) / None (iframe) | AI-assisted triage form |

---

## Developer Guide: Adding a New Tool

Five locations to touch. No existing module files modified.

1. Create `webapp/your_tool_name/routes.py` (and optionally `utils.py`)
2. Create `webapp/templates/includes/your_tool_name_tab.html`
3. Create `webapp/static/js/your_tool_name.js`
4. Register blueprint in `webapp/__init__.py`
5. Add tab entry to `{% set tabs %}` in `index.html` and add `{% elif %}` include block

### Decorator order (non-negotiable)
```python
@blueprint.route('/endpoint', methods=['POST'])  # always first
@require_rc_token                                 # always second
@track_usage('Tool Name')                         # always third
def your_function():
```

### RingCentral API calls
```python
from webapp.rc_api import rc_api_call
data = rc_api_call("/restapi/v1.0/account/~/extension")
# NEVER pass token manually
```

---

## Environment Variables Reference

### Flask app

| Variable | Description |
|---|---|
| `FLASK_SECRET_KEY` | Session signing key |
| `FLASK_ENV` | `development` bypasses Google SSO |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `RC_REDIRECT_URI` | PKCE callback URL |
| `RC_SERVER_URL` | RingCentral API base URL |
| `RC_SCOPE` | OAuth scopes |
| `ADMIN_EMAILS` | Comma-separated admin emails |
| `RCAU_WEBHOOK_SECRET` | Shared secret with gRPC service |
| `GCP_PROJECT_NUMBER` | GCP project number (default: `396158962307`) |
| `GEMINI_API_KEY` | Google Gemini API key (AI Demo Calls + Agent Form) |

### gRPC service (set in Cloud Run console)

| Variable | Description |
|---|---|
| `RCAU_WEBHOOK_URL` | Flask app URL for transcript/dialog webhooks |
| `RCAU_WEBHOOK_SECRET` | Must match Flask app secret |

---

## Local Development Setup

```bash
git clone <repo-url>
cd RCAU
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install google-cloud-speech  # For gRPC server testing
```

`.env` file:
```env
FLASK_ENV=development
FLASK_SECRET_KEY=any-long-random-string
RC_REDIRECT_URI=http://localhost:8080/auth/callback
RC_SERVER_URL=https://platform.ringcentral.com
RC_SCOPE=ReadAccounts ReadCallLog
GOOGLE_CLIENT_ID=your-google-client-id
ADMIN_EMAILS=your@ringcentral.com
RCAU_WEBHOOK_SECRET=your-shared-secret
GCP_PROJECT_NUMBER=396158962307
GEMINI_API_KEY=your-gemini-key
```

Run Flask:
```bash
python3 main.py
```

Run gRPC server (separate terminal):
```bash
cd grpc_streaming
PORT=50051 PYTHONPATH=. python3 main.py
```

---

## Deployment

### Flask app
Push to GitHub → Cloud Build triggers → builds + deploys to AU and UK Cloud Run simultaneously.

The existing Cloud Build trigger has `grpc_streaming/**` in its ignored files filter.

### gRPC streaming service
Separate Cloud Build trigger:
- **Name:** `rcau-grpc-streaming`
- **Included files:** `grpc_streaming/**`
- **Config:** `grpc_streaming/cloudbuild.yaml`
- **Service:** `rcau-rcx-grpc-streaming` in `us-central1`

Set env vars manually in Cloud Run console after first deploy.

---

## RingCX gRPC Streaming Service

### Architecture

```
RingCX call starts
    → Workflow Studio Start Streaming node connects to gRPC service
    → servicer.py receives DialogInit → POSTs dialog_start to Flask
    → servicer.py receives SegmentStart → starts STT per participant
    → servicer.py receives SegmentMedia → feeds audio to STT
    → STT returns transcript → servicer.py POSTs to Flask /transcript-event
    → Flask pushes via SSE to:
        (a) RingCX Streaming tab — supervisor transcript monitor
        (b) Agent Form tab transcript mirror
        (c) agent_form.html iframe — agent's live form with AI suggestions
    → Call ends → servicer.py POSTs dialog_end → Flask removes from active list
```

### Workflow Studio configuration

| Field | Value |
|---|---|
| URL | `grpc://rcau-rcx-grpc-streaming-396158962307.us-central1.run.app:443` |
| Credentials | Basic Auth (any username/password accepted) |
| Segment streaming | Unchecked |

**The `grpc://` scheme is required. `https://` causes STREAMING SETUP FAILED.**

The RCAU streaming tab displays this URL automatically with a copy button.

### Agent Form iframe

The `/agent-form/` route renders a standalone minimal page (no RCAU nav/header) designed for iframe embedding in RingCX agent scripts.

URL format: `https://rcau-api-tools-396158962307.us-central1.run.app/agent-form/?dialog_id={dialogId}&ani={ani}`

RingCX passes `{dialogId}` and `{ani}` as workflow variable substitutions automatically.

The Agent Form tab in RCAU provides a side-by-side debug view with:
- Left: live transcript mirror
- Right: iframe preview of exactly what the agent sees
- Copy button for the iframe URL to paste into agent script config

### AI suggestions

Every 3 final transcript lines, the agent form page calls `POST /agent-form/suggest` with the accumulated transcript. Gemini analyses it and returns suggested values for each triage form field. Suggestions appear as accept/dismiss pills next to each field.

### Proto stubs

Pre-compiled in `grpc_streaming/generated/`. **Do not regenerate** — `streaming_pb2_grpc.py` line 7 has a manual fix: `from generated import streaming_pb2`. Regenerating overwrites this and breaks the import.

### Finding the gRPC service URL

The stable URL is always: `grpc://{service-name}-{project-number}.{region}.run.app:443`

AU: `grpc://rcau-rcx-grpc-streaming-396158962307.us-central1.run.app:443`

Also shown with copy button in the RingCX Streaming tab after connecting.
