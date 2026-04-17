# CLAUDE.md — RCAU API Tools

This file is read automatically by Claude Code at the start of every session.
It provides all context needed to work on this project without re-explanation.

## What this project is

An internal Flask web app for RingCentral Australia engineering staff.
It provides a suite of tools that interact with the RingCentral API on behalf of customer accounts.
Access is restricted to @ringcentral.com Google accounts via SSO.
Deployed on Google Cloud Run, built via Cloud Build on push to GitHub.

Production URL: https://rcau-api-tools-396158962307.us-central1.run.app/

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
- `webapp/static/css/module_name.css` — styles (only if needed)

Shared utilities used by all modules:
- `webapp/rc_api.py` — `rc_api_call()` function for all RingCentral API calls
- `webapp/auth_utils.py` — `@require_rc_token` decorator and auth helpers
- `webapp/usage_tracking.py` — `@track_usage()` decorator, logs to Firestore
- `webapp/firestore_utils.py` — reads app config (passcode, admin list) from Firestore
- `webapp/static/js/app.js` — shared JS: `showMessage()`, `checkRcStatus()`, PKCE connect

New blueprints are registered in `webapp/__init__.py`.
New tabs are added to the `{% set tabs = [...] %}` list in `webapp/templates/index.html`.

## Authentication layers

1. Google SSO — baseline access, all users. Handled by `core/routes.py`. Do not touch.
2. RingCentral PKCE OAuth — most tools. Gives `session['rc_access_token']`. Protect routes with `@require_rc_token`.
3. JWT server-to-server — AI Demo Calls only. Token comes from env vars, not session.

In development mode (`FLASK_ENV=development`), Google SSO is bypassed automatically.
Session is auto-set to: `authenticated=True`, `user_email=developer@local.test`, `is_admin=True`.

## Non-negotiable patterns — follow these exactly

### Decorator order (wrong order causes silent failures)
```python
@blueprint.route('/endpoint', methods=['POST'])  # always first
@require_rc_token                                 # always second
@track_usage('Tool Name')                         # always third (innermost)
def your_function():
```

### Making RingCentral API calls
```python
from webapp.rc_api import rc_api_call

data = rc_api_call("/restapi/v1.0/account/~/sites")
data = rc_api_call("/restapi/v1.0/account/~/extension", params={"perPage": 1000})
result = rc_api_call("/restapi/v1.0/account/~/...", method='POST', json={"key": "val"})
```
NEVER pass the access token manually — rc_api_call() gets it from session automatically.
Passing a token as first argument causes a TypeError.

### Blueprint naming
```python
your_tool_bp = Blueprint('your_tool_bp', __name__, url_prefix='/api/your_tool_name')
```
- Folder: snake_case
- Blueprint variable and name string: snake_case_bp
- URL prefix: /api/folder_name

### Tab registration in index.html
```python
('tab_id', 'Display Name', 'Short description'),
```
The tab_id must exactly match the folder name convention and the {% elif current_tab == 'tab_id' %} include block.

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
        } finally {
            btn.disabled = false;
        }
    });
});
```
Use showMessage(text, isError) from app.js — it is globally available.
Reference static files in templates with: {{ url_for('static', filename='js/file.js') }}

### utils.py decision
Create utils.py if the module makes multiple RC API calls or has data transformation logic.
Keep routes.py thin — just HTTP handling, validation, and calling utils functions.
Routes-only is fine for simple single-call endpoints (see sip_fetcher as example).

## Files that should rarely or never be touched
- `webapp/core/routes.py` — Google SSO, index route
- `webapp/auth/routes.py` — PKCE OAuth flow
- `webapp/__init__.py` — only add new blueprint registrations, never remove existing ones
- `webapp/rc_api.py` — shared API handler
- `webapp/auth_utils.py` — shared auth helpers

## Environment variables
Required always: FLASK_SECRET_KEY, FLASK_ENV, GOOGLE_CLIENT_ID, RC_REDIRECT_URI, RC_SERVER_URL, RC_SCOPE, ADMIN_EMAILS
AI Demo Calls module also needs: DEMO_RC_JWT_AU, DEMO_RC_JWT_UK, DEMO_RC_JWT_US, DEMO_RC_CLIENT_ID, DEMO_RC_CLIENT_SECRET, GEMINI_API_KEY
Analytics module also needs: SM_CLIENT_ID, SM_CLIENT_SECRET
All secrets are in .env locally and injected via Cloud Run environment variables in production.

## Deployment
Push to GitHub → Cloud Build triggers automatically → builds Docker image → pushes to Artifact Registry → deploys to Cloud Run.
No manual deployment steps needed. cloudbuild.yaml handles everything.

## Working style for this project
- Explain what you are going to do and why before making any changes
- Show me the diff and wait for approval before writing to any file
- When making RingCentral API calls in new code, check rc_api.py first so you understand the return types
- If something touches core/, auth/, or __init__.py, flag it and explain why before proceeding
- After completing a change, tell me what to test and how
