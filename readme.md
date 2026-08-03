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
- [Cooperative STOP Facility](#cooperative-stop-facility)
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

### Layer 2 — RingCentral PKCE OAuth (customer-connected tools)
Tools that act on behalf of a specific RingCentral customer account via PKCE flow.
Session keys: `session['rc_access_token']`, `session['rc_refresh_token']`, `session['rc_current_client_id']`
Protect routes with `@require_rc_token`.

Callback URL: `https://rcau-api-tools-396158962307.us-central1.run.app/auth/callback`
Local: `http://localhost:8080/auth/callback`

### Layer 2b — SM Employee Impersonation Bridge (most UC tools)
Most bulk UC tools do **not** connect to the customer via PKCE. Instead the engineer
signs in with their own RingCentral **employee** account and then bridges to a target
customer account by ID. The employee token is exchanged for a customer-scoped
impersonation token via the whitelisted PS bridge (`auth.ps.ringcentral.com`,
`appName: brd`).

Handled by `webapp/auth/routes.py` (`/api/sm_auth/*`) and `webapp/auth_utils.py`.
Session keys: `session['sm_employee_token']` (+ refresh token), `session['sm_isolated_token']`,
`session['sm_target_id']`, `session['sm_target_name']`.

`@require_rc_token` accepts **either** a standard PKCE token or an SM isolated token, and
`rc_api_call()` automatically prefers `sm_isolated_token` over `rc_access_token`. On a `401`
it silently re-mints the bridge (refreshing the employee token first if needed), so
long-running bulk jobs never force the engineer to rebuild the bridge by hand.

Tools requiring this bridge show a "RingCentral Employee Login" gate followed by a
"Target Account Required" prompt (see the `sm_auth_tabs` list in `index.html`).

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
    ├── rc_api.py                    # rc_api_call() — SM-token-aware, auto-refresh on 401
    ├── auth_utils.py                # @require_rc_token + SM impersonation bridge helpers
    ├── task_control.py              # Shared cooperative STOP registry for bulk write tools
    ├── usage_tracking.py
    ├── firestore_utils.py
    │
    ├── core/                        # Index page + Google SSO login/logout (do not touch)
    ├── auth/                        # RC PKCE + SM employee bridge routes (/auth, /api/sm_auth)
    │
    │   # Each tool below is a self-contained blueprint: routes.py (+ optional utils.py),
    │   # a templates/includes/<name>_tab.html partial, and a static/js/<name>.js file.
    │
    │   # ── CC Tools ──
    ├── cxone_audio_converter/       # Convert audio files for CXone
    ├── cxone_script_analyzer/       # CXone changelogs & as-builts
    │
    │   # ── PM Tools ──
    ├── network_requirements/        # Generate customer UC network requirements doc
    ├── port_mapping/                # Map phone numbers from LOA/BRD
    │
    │   # ── SE Tools ──
    ├── account_health/              # Account Discovery — pre-engagement analysis
    ├── agent_form/                  # Agent form tab + standalone iframe page
    ├── ai_demo_calls/               # Generate AI demo calls (JWT auth)
    ├── click_to_call/               # Trigger outbound call via RingCX dialer
    ├── d365_ringcx/                 # Dynamics 365 + RingCX demo (leads, scoring, routing)
    ├── audio_streaming/             # RingCX live transcript streaming tab
    │
    │   # ── UC Tools (most use the SM impersonation bridge) ──
    ├── air_management/              # Bulk AI Receptionist audit/create/modify
    ├── presence/                    # BLF & Presence — audit monitored lines
    ├── bulk_hours/                  # Bulk Opening Hours (Sites/Queues)
    ├── analytics/                   # Business Analytics reports
    ├── visualiser/                  # Call Flow Visualiser
    ├── cost_centres/                # Audit/update Cost Centre allocations
    ├── custom_rules/                # Bulk answering rules via CSV
    ├── device_audit/                # Audit provisioned devices + online status
    ├── device_ringing_audit/        # Query V1/V2 call-handling ringing states
    ├── device_swap/                 # Swap DLs/Extensions
    ├── extension_number_changer/    # Bulk change extension numbers via Excel
    ├── extension_renamer/           # Bulk edit extension names
    ├── live_events/                 # Real-time subscription listener
    ├── message_management/          # Greeting Studio — audit/apply/export audio
    ├── personal_address_book/       # Multi-user address book
    ├── notifications/               # Audit/update notification prefs
    ├── phone_number_assignment/     # Bulk assign numbers from inventory via Excel
    ├── ringex_uat/                  # Generate UAT scripts
    ├── sip_fetcher/                 # Fetch SIP credentials
    ├── account_migration/           # Export account programming/settings/audio
    ├── cq_hours/                    # Call Queue Manager — hours/routing/timers/limits
    ├── site_allocation/             # Bulk re-allocate any extension to a Site
    ├── user_templates/              # Bulk apply User Templates via Excel
    │
    ├── static/js/
    │   ├── app.js                   # Shared: showMessage(), checkRcStatus(), PKCE connect
    │   └── <tool_name>.js           # One JS file per tool
    │
    └── templates/
        ├── index.html              # Grouped sidebar nav (CC/PM/SE/UC) + SM auth gate
        ├── agent_form.html         # Standalone iframe page (no RCAU chrome)
        └── includes/
            └── <tool_name>_tab.html # One UI partial per tool
```

---

## Current Tools

Tools are grouped in the sidebar by function: **CC** (Contact Centre), **PM** (Project
Management), **SE** (Sales Engineering), and **UC** (Unified Comms). The **Auth** column:
`L1` Google SSO only · `L2` RC PKCE OAuth · `SM` SM employee impersonation bridge (see
Layer 2b) · `L3` JWT · `L4` RingCX token exchange.

### Authentication (sidebar)

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `auth_rex` | REX Authentication | L1 | RC PKCE OAuth connection |
| `auth_cxone` | RCCC Authentication | L1 | CXone authentication |

### CC Tools

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `cxone_audio_converter` | CXone Audio Converter | L2 | Convert audio files for CXone |
| `cxone_script_analyzer` | CXone Script Analyzer | L2 | CXone changelogs & as-builts |

### PM Tools

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `network_requirements` | Network Requirements | L2 | Generate customer UC network requirements doc |
| `port_mapping` | Port Mapping | SM | Map phone numbers from LOA/BRD |

### SE Tools

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `account_discovery` | Account Discovery | L2 | Pre-engagement account analysis |
| `agent_form` | Agent Form | L1 (tab) / None (iframe) | AI-assisted triage form |
| `ai_demo_calls` | AI Demo Calls | L3 | Generate AI demo calls |
| `click_to_call` | Click to Call | L2 | Trigger an outbound call via the RingCX dialer |
| `d365_ringcx` | D365 RingCX Demo | L2 | Dynamics 365 + RingCX demo (leads, scoring, routing) |
| `audio_streaming` | RingCX Streaming | L4 | Live call transcript monitor |

### UC Tools

| Tab ID | Display Name | Auth | Description |
|---|---|---|---|
| `air_management` | AIR Management | SM | Bulk audit/create/modify AI Receptionists |
| `presence` | BLF & Presence | SM | Audit BLF monitored lines |
| `bulk_opening` | Bulk Opening Hours | SM | Mass Site/Queue hours config |
| `analytics` | Business Analytics | L2 | Call performance reports |
| `call_flow` | Call Flow Visualiser | SM | Visual routing path |
| `cost_centres` | Cost Centres | SM | Audit/update Cost Centre allocations |
| `custom_rules` | Custom Rules | SM | Bulk answering rules via CSV |
| `device_audit` | Device Audit | SM | Audit provisioned devices + online status |
| `device_ringing_audit` | Device Ringing Audit | SM | Query V1/V2 call-handling ringing states |
| `device_swap` | Device Swap | SM | Swap DLs/Extensions |
| `extension_number_changer` | Ext Number Changer | SM | Bulk change extension numbers via Excel |
| `renamer` | Extension Renamer | SM | Bulk edit extension names |
| `live_events` | Live Events | SM | Real-time subscription listener |
| `message_management` | Message Management | SM | Greeting Studio — audit/apply/export audio |
| `personal_address_book` | Multi User Address Book | SM | Multi-user address book |
| `notifications` | Notifications | SM | Audit/update notification prefs |
| `phone_number_assignment` | Phone Number Assignment | SM | Bulk assign numbers from inventory via Excel |
| `ringex_uat` | RingEX UAT | SM | Generate UAT scripts |
| `sip_fetcher` | SIP Credentials | SM | Fetch SIP credentials |
| `account_migration` | Account Migration | SM | Export account programming/settings/audio |
| `cq_hours` | Call Queue Manager | SM | Bulk hours/routing/timers/limits for Call Queues |
| `site_allocation` | Site Allocation | SM | Bulk re-allocate any extension to a Site |
| `user_templates` | User Templates | SM | Bulk apply User Templates via Excel |

Admins additionally see an **Admin Dashboard** tab (`admin_dashboard`).

---

## Developer Guide: Adding a New Tool

Five locations to touch. No existing module files modified.

1. Create `webapp/your_tool_name/routes.py` (and optionally `utils.py`)
2. Create `webapp/templates/includes/your_tool_name_tab.html`
3. Create `webapp/static/js/your_tool_name.js`
4. Register blueprint in `webapp/__init__.py`
5. Add the tab to the appropriate `nav_groups` group (CC/PM/SE/UC) in `index.html`,
   add an `{% elif current_tab == 'your_tool_name' %}` include block, and — if the tool
   should act on customers via the employee bridge rather than PKCE — add its tab ID to the
   `sm_auth_tabs` list.

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
# NEVER pass token manually — rc_api_call() picks the right session token
# (SM impersonation token preferred, else PKCE token) and refreshes on 401.
```

---

## Cooperative STOP Facility

Any tool that writes to an account in bulk (item by item, in batches, or as a streamed
job) should be stoppable mid-run. Rather than each tool re-inventing a cancel flag, they
share one in-memory registry: `webapp/task_control.py`, keyed by a `task_id`.

This is safe because the app runs as a **single** Gunicorn process with threads
(`--workers 1 --threads 8`, see the dockerfile), so the worker doing the writes and the
`/cancel` request that stops it share the same memory. If the app ever moves to multiple
worker processes, this registry must move to a shared backend (Firestore/Redis).

Cancellation is cooperative and best-effort: a worker checks `is_stopped()` between items
and bails cleanly. Items already sent to RingCentral cannot be recalled — only not-yet-sent
items are skipped.

```python
from webapp import task_control

# Worker loop (background thread, generator, or synchronous request)
for item in items:
    if task_control.is_stopped(task_id):
        break                       # record a 'stopped' outcome, then stop
    ... write item ...
task_control.clear(task_id)         # always clear when the task ends

# Expose a /cancel endpoint in one line
bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])
```

Use `task_control.interruptible_sleep(task_id, seconds)` instead of `time.sleep()` when a
tool waits out RingCentral rate limits or an account-wide apply lock, so a stop request is
honoured promptly rather than after a multi-minute wait.

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
| `SM_CLIENT_ID` | RC OAuth client ID for the SM employee impersonation bridge |
| `SM_CLIENT_SECRET` | RC OAuth client secret for the SM bridge (optional — public clients pass the ID in-body) |
| `DEMO_RC_JWT_AU` / `_UK` / `_US` | Static RC JWTs for AI Demo Calls (Layer 3) |
| `DEMO_RC_CLIENT_ID` / `DEMO_RC_CLIENT_SECRET` | JWT app credentials for AI Demo Calls |

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
SM_CLIENT_ID=your-sm-bridge-client-id
SM_CLIENT_SECRET=your-sm-bridge-client-secret
```

> In `FLASK_ENV=development`, Google SSO is bypassed (`authenticated=True`,
> `user_email=developer@local.test`, `is_admin=True`). The SM employee bridge and RC PKCE
> flows still require real RingCentral credentials to reach live accounts.

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
