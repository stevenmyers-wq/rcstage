# RingCentral Australia Engineering API Tools (RCAU)

An internal web application providing a suite of tools for interacting with the RingCentral API. Designed for RingCentral support, administration, and engineering staff. Access is restricted to `@ringcentral.com` Google accounts.

**Production URL:** `https://rcau-api-tools-396158962307.us-central1.run.app/`

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

---

## Architecture Overview

The application is a **modular Flask Blueprint** app. Each tool is a fully self-contained module with its own backend routes, business logic, HTML template partial, and JavaScript file. Modules share a small set of common utilities but are otherwise independent — adding or changing one tool does not affect any other.

**Tech stack:**
- **Backend:** Python 3.11, Flask, Gunicorn
- **Frontend:** Jinja2 templates, Tailwind CSS (loaded via CDN — no build step), vanilla JavaScript
- **Database:** Google Cloud Firestore (usage tracking and config)
- **Deployment:** Docker → Google Artifact Registry → Google Cloud Run, triggered by Cloud Build on push

---

## Authentication Models

The app has three distinct auth layers. Understanding which layer a tool uses determines how you structure its backend routes.

### Layer 1 — Website Auth (Google SSO)
All users must sign in with a `@ringcentral.com` Google account. This is handled entirely by `webapp/core/routes.py` and `webapp/auth/routes.py`. **Do not modify these files** when adding new tools.

Session key set: `session['authenticated']`, `session['user_email']`, `session['is_admin']`

### Layer 2 — RingCentral PKCE OAuth (most tools)
Most tools act on behalf of a specific RingCentral *customer* account. The user provides a Client ID from an OAuth app created in the customer's RingCentral Developer Portal, and the app completes a PKCE flow to get an access token.

Session key set: `session['rc_access_token']`, `session['rc_client_id']`

To protect a route that requires this layer, use the `@require_rc_token` decorator (see [Key Development Patterns](#key-development-patterns)).

**Required RingCentral App Permissions (scopes) for customer OAuth apps:**
- `ReadAccounts`
- `ReadCallLog`
- `EditExtensions`
- `ReadPresence`
- `Contacts`
- `EditCustomData`

The callback URL to whitelist in the RingCentral Developer Portal:
`https://rcau-api-tools-396158962307.us-central1.run.app/auth/callback`

For local development: `http://localhost:8080/auth/callback`

### Layer 3 — JWT Server-to-Server (AI Demo Calls only)
The AI Demo Calls module uses static RingCentral JWTs injected via Cloud Run environment variables. It does **not** use the PKCE flow. See `webapp/ai_demo_calls/utils.py` and the [Environment Variables Reference](#environment-variables-reference).

---

## Project Structure

```
RCAU-main/
├── main.py                          # App entry point — starts Flask/Gunicorn
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container definition
├── cloudbuild.yaml                  # Cloud Build CI/CD pipeline
├── .gitignore
│
└── webapp/                          # Main Flask application package
    ├── __init__.py                  # App factory — registers ALL blueprints here
    ├── rc_api.py                    # Shared RingCentral API call handler
    ├── auth_utils.py                # Shared auth decorators and helpers
    ├── usage_tracking.py            # Firestore usage logging + @track_usage decorator
    ├── firestore_utils.py           # Firestore config (passcode, admin list)
    │
    ├── static/
    │   ├── css/
    │   │   ├── styles.css           # Global shared styles
    │   │   ├── bulk_hours.css       # Module-specific styles
    │   │   └── visualiser.css
    │   ├── js/
    │   │   ├── app.js               # Shared JS: showMessage(), checkRcStatus(), PKCE connect
    │   │   ├── bulk_hours.js
    │   │   ├── visualiser.js
    │   │   ├── live_events.js
    │   │   ├── notifications.js
    │   │   ├── personal_address_book.js
    │   │   └── ai_demo_calls.js
    │   └── audio/                   # Gitignored except .gitkeep
    │
    ├── templates/
    │   ├── index.html               # Main app shell, sidebar nav, tab rendering
    │   ├── error.html
    │   └── includes/                # One HTML partial per tool tab
    │       ├── authenticator_tab.html
    │       ├── sip_fetcher_tab.html
    │       ├── device_swap_tab.html
    │       ├── extension_renamer_tab.html
    │       ├── bulk_opening_tab.html
    │       ├── call_flow_tab.html
    │       ├── personal_address_book_tab.html
    │       ├── live_events_tab.html
    │       ├── custom_rules_tab.html
    │       ├── notifications_tab.html
    │       ├── greetings_uploader_tab.html
    │       ├── ringex_uat_tab.html
    │       ├── ai_demo_calls_tab.html
    │       └── analytics_tab.html
    │
    ├── core/                        # Index route, Google SSO handler — rarely touch
    │   └── routes.py
    ├── auth/                        # PKCE OAuth flow — rarely touch
    │   └── routes.py
    │
    ├── bulk_hours/                  # Example of a full module (routes + utils)
    │   ├── routes.py
    │   └── utils.py
    ├── visualiser/
    │   ├── routes.py
    │   └── utils.py
    ├── custom_rules/
    │   ├── routes.py
    │   └── utils.py
    ├── notifications/
    │   ├── routes.py
    │   └── utils.py
    ├── personal_address_book/
    │   ├── routes.py
    │   └── utils.py
    ├── greetings_uploader/
    │   ├── routes.py
    │   └── utils.py
    ├── ringex_uat/
    │   ├── routes.py
    │   └── utils.py
    ├── ai_demo_calls/
    │   ├── routes.py
    │   └── utils.py
    ├── analytics/
    │   ├── routes.py
    │   └── utils.py
    ├── extension_renamer/
    │   ├── routes.py
    │   └── utils.py
    ├── sip_fetcher/                 # Example of a routes-only module (no utils.py)
    │   └── routes.py
    └── live_events/
        ├── routes.py
        └── utils.py
```

---

## Current Tools

| Tab ID | Display Name | Auth Layer | Description |
|---|---|---|---|
| `authenticator` | PKCE Setup | Layer 1 | Manages RingCentral OAuth connection |
| `sip_fetcher` | SIP Credentials | Layer 2 | Fetches SIP credentials for devices |
| `device_swap` | Device Swap | Layer 2 | Swaps DLs/Extensions between two devices |
| `renamer` | Extension Renamer | Layer 2 | Bulk edit extension names |
| `bulk_opening` | Bulk Opening Hours | Layer 2 | Mass configuration for Sites/Queues hours and rules |
| `call_flow` | Call Flow Visualiser | Layer 2 | Displays visual routing path for any number |
| `personal_address_book` | Multi User Address Book | Layer 2 | Multi-user personal address book tool |
| `live_events` | Live Events | Layer 2 | Real-time subscription listener |
| `custom_rules` | Custom Rules | Layer 2 | Bulk update answering rules via CSV |
| `notifications` | Notifications | Layer 2 | Audit and bulk update notification preferences |
| `greetings_uploader` | Greetings Uploader | Layer 2 | Upload greetings to Message-Only/Announcement extensions |
| `ringex_uat` | RingEX UAT | Layer 2 | Generate UAT scripts from routing rules |
| `ai_demo_calls` | AI Demo Calls | Layer 3 (JWT) | Generate and play demo calls on EX and CX |
| `analytics` | Business Analytics | Layer 2 | Aggregated call performance reports |

---

## Developer Guide: Adding a New Tool

Adding a new tool requires touching exactly **5 locations**. No existing module files are modified.

### Step 1 — Create the backend module folder

Create `webapp/your_tool_name/routes.py`.

**When to also create `utils.py`:** If your tool makes multiple RingCentral API calls or has meaningful data transformation logic, put that in `utils.py` and keep `routes.py` thin (just HTTP handling). If it's a simple single-call endpoint, routes-only is fine (see `sip_fetcher` as an example).

```python
# webapp/your_tool_name/routes.py
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils  # Only if you have a utils.py

your_tool_bp = Blueprint('your_tool_bp', __name__, url_prefix='/api/your_tool_name')

@your_tool_bp.route('/some-action', methods=['POST'])
@require_rc_token
@track_usage('Your Tool Display Name')
def some_action():
    # Your logic here
    return jsonify({"status": "success"})
```

**Naming conventions:**
- Folder name: `snake_case` (e.g. `contact_uploader`)
- Blueprint variable: `snake_case_bp` (e.g. `contact_uploader_bp`)
- URL prefix: `/api/your_folder_name`
- Blueprint name string: same as variable name (e.g. `'contact_uploader_bp'`)

### Step 2 — Create the HTML template partial

Create `webapp/templates/includes/your_tool_name_tab.html`.

Load your JS and CSS at the top of this file using Jinja2's `url_for`. They will only load when this tab is active.

```html
<!-- webapp/templates/includes/your_tool_name_tab.html -->
<script src="{{ url_for('static', filename='js/your_tool_name.js') }}" defer></script>
<!-- Only include CSS link if you have module-specific styles -->
<link rel="stylesheet" href="{{ url_for('static', filename='css/your_tool_name.css') }}">

<div class="space-y-6">
    <h3 class="text-xl font-bold text-blue-700">Your Tool Name</h3>
    <!-- Your UI here. Use Tailwind utility classes for styling. -->
</div>
```

### Step 3 — Create frontend assets (if needed)

Create `webapp/static/js/your_tool_name.js`. See [Frontend Patterns](#frontend-patterns) for how to structure it.

Only create `webapp/static/css/your_tool_name.css` if you need styles that Tailwind utility classes cannot cover.

### Step 4 — Register the blueprint in the app factory

Open `webapp/__init__.py` and add two lines following the existing pattern:

```python
# In webapp/__init__.py, inside create_app(), inside the with app.app_context(): block
from .your_tool_name import routes as your_tool_name_routes
app.register_blueprint(your_tool_name_routes.your_tool_bp)
```

**Important:** If you forget this step, the tab will render but all API calls will return 404 with no other error.

### Step 5 — Add the tab to the sidebar navigation

Open `webapp/templates/index.html` and find the `{% set tabs = [ ... ] %}` block. Add your entry as a tuple:

```python
('your_tab_id', 'Display Name', 'Short description shown on hover'),
```

**Critical:** The first value (`your_tab_id`) must exactly match:
1. The `tab` query parameter your routes expect
2. The name used in the `{% include %}` call that renders your template partial

The includes block below the tabs list maps tab ID to template file. Add your include there too:

```html
{% elif current_tab == 'your_tab_id' %}
    {% include 'includes/your_tool_name_tab.html' %}
```

---

## Key Development Patterns

### Decorator order matters

Always apply decorators in this exact order:

```python
@blueprint.route('/endpoint', methods=['POST'])  # 1. Route — always first
@require_rc_token                                 # 2. Auth check — before usage tracking
@track_usage('Tool Name')                         # 3. Usage logging — innermost
def your_function():
    ...
```

Getting the order wrong causes silent failures or incorrect usage logging.

### `@require_rc_token` — protecting routes

Import from `webapp.auth_utils`. Apply to any route that calls the RingCentral API on behalf of a customer. Returns a clean 401 JSON response if the user hasn't completed PKCE auth.

```python
from webapp.auth_utils import require_rc_token

@your_tool_bp.route('/get-data')
@require_rc_token
def get_data():
    # Only runs if rc_access_token exists in session
    ...
```

Do not use this on Layer 3 (JWT/AI Demo Calls) endpoints — those use their own token from environment variables.

### `rc_api_call()` — making RingCentral API requests

Import from `webapp.rc_api`. This function automatically retrieves the access token from the user's session. **Never pass the token manually** — it will cause a TypeError.

```python
from webapp.rc_api import rc_api_call

# Correct
data = rc_api_call("/restapi/v1.0/account/~/extension")

# Correct — with query params
data = rc_api_call("/restapi/v1.0/account/~/extension", params={"perPage": 1000})

# Correct — POST with JSON body
result = rc_api_call("/restapi/v1.0/account/~/...", method='POST', json={"key": "value"})

# Wrong — do not do this
token = session.get('rc_access_token')
data = rc_api_call(token, "/restapi/v1.0/account/~/extension")  # TypeError
```

The function returns a parsed JSON dict on success, or `None` on failure (use `raise_error=True` to raise instead).

### `@track_usage()` — logging tool usage

Import from `webapp.usage_tracking`. Apply to the primary action endpoint of every tool. Pass a human-readable tool name that will appear in the Admin Analytics dashboard.

```python
from webapp.usage_tracking import track_usage

@your_tool_bp.route('/execute', methods=['POST'])
@require_rc_token
@track_usage('Your Tool Name')
def execute():
    ...
```

### Firestore utilities

There are two Firestore helpers — use the right one:

| File | Use for |
|---|---|
| `webapp/usage_tracking.py` | The `@track_usage` decorator. Do not call directly for new tools. |
| `webapp/firestore_utils.py` | Reading app config (passcode, admin email list). Use `get_config_from_firestore()`. |

Do not create a third Firestore client. If you need to store tool-specific data in Firestore, add a function to `firestore_utils.py`.

---

## Frontend Patterns

The app uses **vanilla JavaScript** with no framework. Tailwind CSS is loaded via CDN — no build step, no `package.json`.

### Shared functions from `app.js`

`app.js` is loaded globally. These functions are available on every page:

```javascript
// Show a toast notification (green = success, red = error)
showMessage('Your message here', false);       // success
showMessage('Something went wrong', true);     // error

// Check RingCentral connection status and update the UI card
checkRcStatus(); // Returns a Promise<boolean>
```

### Standard fetch pattern for a tool's JS file

Wrap all logic in `DOMContentLoaded`. Use `async/await` for API calls. Always call `showMessage()` on success and error.

```javascript
// webapp/static/js/your_tool_name.js
document.addEventListener('DOMContentLoaded', () => {

    const fetchBtn = document.getElementById('your-fetch-btn');
    if (!fetchBtn) return; // Guard: exit if tab elements not present

    fetchBtn.addEventListener('click', async () => {
        fetchBtn.disabled = true;
        fetchBtn.textContent = 'Loading...';

        try {
            const response = await fetch('/api/your_tool_name/some-action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'value' })
            });

            const data = await response.json();

            if (response.ok) {
                showMessage('Action completed successfully.');
                // Update your UI with data
            } else {
                showMessage(data.error || 'An error occurred.', true);
            }
        } catch (err) {
            showMessage('Network error. Please try again.', true);
            console.error(err);
        } finally {
            fetchBtn.disabled = false;
            fetchBtn.textContent = 'Fetch Data';
        }
    });

});
```

### Referencing static files in templates

Always use Jinja2's `url_for` — never hardcode paths:

```html
<script src="{{ url_for('static', filename='js/your_tool_name.js') }}" defer></script>
<link rel="stylesheet" href="{{ url_for('static', filename='css/your_tool_name.css') }}">
```

---

## Environment Variables Reference

### Required for all environments

| Variable | Description | Example |
|---|---|---|
| `FLASK_SECRET_KEY` | Strong random string for signing Flask sessions | `openssl rand -hex 32` |
| `FLASK_ENV` | Set to `development` locally to bypass Google SSO | `development` |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID for SSO | From Google Cloud Console |
| `RC_REDIRECT_URI` | Full callback URL whitelisted in RingCentral Dev Portal | `http://localhost:8080/auth/callback` |
| `RC_SERVER_URL` | RingCentral API base URL | `https://platform.ringcentral.com` |
| `RC_SCOPE` | OAuth scopes requested during PKCE flow | `ReadAccounts ReadCallLog` |
| `ADMIN_EMAILS` | Comma-separated list of admin user emails | `user@ringcentral.com,other@ringcentral.com` |

### Required for specific modules

| Variable | Module | Description |
|---|---|---|
| `DEMO_RC_JWT_AU` | AI Demo Calls | JWT for AU demo RingCentral account |
| `DEMO_RC_JWT_UK` | AI Demo Calls | JWT for UK demo RingCentral account |
| `DEMO_RC_JWT_US` | AI Demo Calls | JWT for US demo RingCentral account |
| `DEMO_RC_CLIENT_ID` | AI Demo Calls | Client ID for JWT server-to-server app |
| `DEMO_RC_CLIENT_SECRET` | AI Demo Calls | Client secret for JWT server-to-server app |
| `GEMINI_API_KEY` | AI Demo Calls | Google Gemini API key |
| `SM_CLIENT_ID` | Analytics | Analytics service client ID |
| `SM_CLIENT_SECRET` | Analytics | Analytics service client secret |

### Development mode behaviour

When `FLASK_ENV=development`, the Google SSO check is bypassed entirely. The app auto-sets:
- `session['authenticated'] = True`
- `session['user_email'] = 'developer@local.test'`
- `session['is_admin'] = True`

This means you can run and test locally without valid Google credentials.

---

## Local Development Setup

### Prerequisites

- Python 3.11+
- pip
- git

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd RCAU-main
python3 -m venv venv
source venv/bin/activate
```

Your terminal prompt will show `(venv)` when the environment is active.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your `.env` file

Create a file named `.env` in the project root. It is already gitignored.

```env
# Bypass Google SSO for local development
FLASK_ENV=development

# Required — use any strong random string locally
FLASK_SECRET_KEY=any-long-random-string-here

# Required — must match what's whitelisted in your RingCentral Dev App
RC_REDIRECT_URI=http://localhost:8080/auth/callback

# RingCentral API base URL
RC_SERVER_URL=https://platform.ringcentral.com

# Scopes requested during PKCE
RC_SCOPE=ReadAccounts ReadCallLog

# Required for Google SSO (not needed locally if FLASK_ENV=development)
GOOGLE_CLIENT_ID=your-google-client-id

# Comma-separated admin emails
ADMIN_EMAILS=your@ringcentral.com

# Only needed if testing AI Demo Calls locally
# DEMO_RC_JWT_AU=
# DEMO_RC_JWT_UK=
# DEMO_RC_JWT_US=
# DEMO_RC_CLIENT_ID=
# DEMO_RC_CLIENT_SECRET=
# GEMINI_API_KEY=
```

### 4. Run the app

```bash
python3 main.py
```

Access at `http://localhost:8080`. In development mode, you are automatically logged in as an admin — no Google account required.

---

## Deployment

Deployment is fully automated. Push to the connected GitHub branch and Cloud Build handles everything:

1. Builds the Docker container image
2. Pushes the image to Google Artifact Registry (`us-central1-docker.pkg.dev/sr-1906369/rcau-repo/rcau-api-tools`)
3. Deploys the new image as a revision to the Cloud Run service (`rcau-api-tools`, `us-central1`)

Environment variables and secrets are configured in Cloud Run directly — they are not in the codebase.

The `cloudbuild.yaml` tags each image with the Git commit SHA (`$COMMIT_SHA`), making rollbacks straightforward via the Cloud Run console.
