# RingCentral Australia Engineering API Tools

This web application provides a suite of tools for interacting with the RingCentral API, designed for support, administration, and development tasks. The application is built with a modular architecture to allow for easy maintenance and independent development of new features.
Website to access: `https://rcau-api-tools-396158962307.us-central1.run.app/`
Login email: `your email` - any works. Its just for tracking.
Passcode: `serotonin-human-prodigy-croon-palace-scheming`

## Key Features

* **Secure Two-Layer Authentication:**
    * A shared passcode to unlock the website.
    * An OAuth 2.0 PKCE flow to securely connect to a RingCentral account.
* **Call Flow Visualiser:** An interactive tool that traces and displays the complete call routing path for any phone number in the account.
* **Modular Tool Tabs:** The application is designed to be easily extensible with new tools, each contained within its own tab. Current placeholders include:
    * SIP Credentials Fetcher
    * Device Swap Utility
    * Bulk Opening Hours Management
    * Live Events Listener
    * Custom Extension Rules

## RingCentral Developer Portal Setup
   * Create a new API application on the customers UID
   * Enter the following as the callback URL - https://rcau-api-tools-396158962307.us-central1.run.app/auth/callback
   * Set App Permissions

## Project Structure

The project follows a modular Flask Blueprint architecture. Each major feature is a self-contained module, promoting a clean separation of concerns.

```
|
├── main.py                 # Main application entry point
├── requirements.txt        # Python package dependencies
├── Dockerfile              # Container definition for deployment
├── cloudbuild.yaml         # Google Cloud Build configuration
|
└── /webapp                 # Main Flask application package
    |
    ├── __init__.py         # Application factory, registers all blueprints
    |
    ├── /static/            # All frontend assets (CSS, JS, images)
    │   ├── /css/
    │   │   └── visualiser.css
    │   └── /js/
    │       ├── app.js      # Core JS for login, auth, and main layout
    │       └── visualiser.js
    |
    ├── /templates/         # HTML templates
    │   ├── index.html      # The main application shell and tab navigation
    │   ├── error.html
    │   └── /includes/      # HTML partials for each tool's tab content
    │       ├── authenticator_tab.html
    │       ├── call_flow_tab.html
    │       └── ... (other tool tabs)
    |
    ├── /core/              # Core functionality (Homepage, Website Auth)
    │   └── routes.py
    |
    ├── /auth/              # Handles RingCentral PKCE OAuth flow
    │   └── routes.py
    |
    ├── /visualiser/        # Call Flow Visualiser module
    │   ├── routes.py       # API endpoints for the visualiser
    │   └── utils.py        # Backend helper functions for tracing call flows
    |
    ├── /sip_fetcher/       # SIP Credentials Fetcher module
    │   └── routes.py
    |
    ├── auth_utils.py       # Shared authentication helper functions
    ├── firestore_utils.py  # Shared Firestore database functions
    └── rc_api.py           # Shared, generic RingCentral API call handler
```

## Developer's Guide: Working Independently

The modular design is intended to allow developers to work on features without causing conflicts. Here’s how to approach different tasks.

### Core Principle: Separation of Concerns

The `core` and `auth` modules should rarely be touched. They handle the fundamental login and connection logic for the entire site. Each tool is a self-contained unit. It has its own backend routes, frontend assets, and HTML template partial.

### How to Modify an Existing Tool (Example: Call Flow Visualiser)

If you need to fix a bug or add a feature to the Call Flow Visualiser, you only need to work within its dedicated files. You can safely ignore all other modules.

* **Backend API Logic:**
    * Modify the API endpoints in `webapp/visualiser/routes.py`.
    * Update the call flow tracing logic in `webapp/visualiser/utils.py`.
* **Frontend Interaction & Display:**
    * Change the UI layout in `webapp/templates/includes/call_flow_tab.html`.
    * Update the JavaScript that fetches data and renders the diagram in `webapp/static/js/visualiser.js`.
    * Adjust the flowchart styling in `webapp/static/css/visualiser.css`.

By staying within these files, your work will not affect the SIP Fetcher, Device Swap, or any other tool.

### How to Add a Brand New Tool (Example: "Contact Uploader")

Here is the checklist for adding a new feature to the application.

1.  **Create the Backend Module**
    * Create a new folder: `webapp/contact_uploader/`.
    * Inside it, create `webapp/contact_uploader/routes.py`. Define a new Flask Blueprint and add your API endpoints (e.g., `/api/rc/upload-contacts`).
2.  **Create the Frontend Template**
    * Create a new HTML file: `webapp/templates/includes/contact_uploader_tab.html`. This file will contain the UI for your new tool (e.g., a file input and an "Upload" button).
3.  **(If Needed) Create Frontend Assets**
    * If your tool needs specific JavaScript or CSS, create new files:
        * `webapp/static/js/contact_uploader.js`
        * `webapp/static/css/contact_uploader.css`
    * **Important:** Link these new static files directly inside your `contact_uploader_tab.html` file. This ensures they are only loaded when the user clicks on your tool's tab.
4.  **Register the New Module**
    * Open `webapp/__init__.py`.
    * Import and register the new Blueprint you created in Step 1.
5.  **Add the Tab to the Main UI**
    * Open `webapp/templates/index.html`.
    * Add your new tool to the `tabs` list to make it appear in the navigation bar.

Your new tool is now integrated without having modified any of the existing tools' code.

---

### **Key Development Patterns for New Features**

When adding new API-driven features, you **must** adhere to these established patterns to ensure your code integrates smoothly and securely. These conventions are critical for maintaining the application's stability.

#### **1. Securing API Endpoints with `@require_rc_token`**

All backend routes that require a connection to the RingCentral API **must** be protected by the `@require_rc_token` decorator.

* **Location:** `webapp/auth_utils.py`
* **Purpose:** This decorator acts as a security guard for the Layer 2 (RingCentral) connection. It automatically checks if a valid access token exists in the user's session before executing the function. If no token is found, it cleanly returns a `401 Unauthorized` JSON error, preventing API calls from failing and the app from crashing.
* **Implementation:**
    ```python
    # In your new routes.py file (e.g., webapp/new_tool/routes.py)
    from flask import Blueprint, jsonify
    from webapp.auth_utils import require_rc_token # <-- Import the decorator

    new_tool_bp = Blueprint('new_tool_bp', __name__)

    @new_tool_bp.route('/api/new_tool/get-data')
    @require_rc_token # <-- Apply the decorator to your route
    def get_some_data():
        # Your code here will only run if the user is connected to RingCentral.
        return jsonify({"status": "success"})
    ```

#### **2. Making RingCentral API Calls with `rc_api_call()`**

The project includes a centralized, intelligent helper function for all communication with the RingCentral API.

* **Location:** `webapp/rc_api.py`
* **Function:** `rc_api_call()`
* **Critical Usage Note:** This function **automatically retrieves the access token from the user's session**. You **DO NOT** need to pass the access token to it. Passing the token manually will cause a `TypeError` and break the application.
* **Correct Usage:**
    ```python
    # In your new utils.py or routes.py file
    from webapp.rc_api import rc_api_call # <-- Import the helper

    def get_sites_list():
        # Correct: Simply provide the API endpoint.
        sites_data = rc_api_call("/restapi/v1.0/account/~/sites")
        
        # ... process sites_data ...
        return sites_data
    ```
* **Incorrect Usage (Will Cause Errors):**
    ```python
    # DO NOT DO THIS
    from flask import session
    
    def get_sites_list_incorrectly():
        access_token = session.get('rc_access_token')
        # Incorrect: Passing the token will break the function call.
        sites_data = rc_api_call(access_token, "/restapi/v1.0/account/~/sites") # <-- WRONG
        return sites_data
    ```

---

### **AI-Assisted Development (Gemini Prompt Template)**

To get the best results when using an AI assistant like Gemini to generate a new tool for this project, you must provide a detailed initial prompt. A high-quality prompt eliminates guesswork and produces code that is compatible with our framework.

**How to create the prompt:**

1.  Start a new chat with the AI.
2.  Copy the entire **"Prompt Template"** block below.
3.  Paste it into the chat.
4.  Replace the placeholder text with your specific goal.
5.  **Crucially**, go to the `[PASTE README HERE]` and `[PASTE EXAMPLE FILES HERE]` sections and paste the full, plain text content of the required files directly into the prompt.

---

#### **Prompt Template (Copy this entire block)**

**Goal:** I want to add a new tool to my "RingCentral Australia Engineering API Tools" application. The new tool will be called "**[Your Tool Name]**" and its purpose is to **[clearly describe what the tool does, e.g., "bulk delete all call recordings for a list of users"]**.

**Project Context:**
Here is the `README.md` for my project. Please follow its file structure and development patterns carefully when creating the new files.

```markdown
[PASTE THE ENTIRE CONTENTS OF README.md HERE]
```

**Golden Example of a Correctly Implemented Module (`webapp/bulk_hours/`)**
Here are the contents of a working module. Please use this as the primary reference for how to structure the new tool's code, especially for using decorators and the shared API call function.

**`webapp/bulk_hours/routes.py`**
```python
[PASTE THE ENTIRE CONTENTS OF webapp/bulk_hours/routes.py HERE]
```

**`webapp/bulk_hours/utils.py`**
```python
[PASTE THE ENTIRE CONTENTS OF webapp/bulk_hours/utils.py HERE]
```

**Key Instructions for AI:**
1.  Create all necessary files for the new "**[Your Tool Name]**" module.
2.  All new backend API endpoints that interact with RingCentral **must** be protected with the `@require_rc_token` decorator.
3.  All calls to the RingCentral API **must** use the shared helper function `rc_api_call()`.
4.  **Do not pass the access token manually to the `rc_api_call()` function.** It retrieves the token from the session automatically.
5.  Provide the necessary changes for `webapp/__init__.py` to register the new blueprint and for `webapp/templates/index.html` to add the new tab to the navigation.
6.  Use the `webapp/bulk_hours/` module as the primary example of a correctly implemented tool.

---

## Local Development Setup

This guide provides instructions for setting up the project for local development on an Ubuntu environment.

### Prerequisites

* Python 3.10+
* `pip` for package management
* `git` for version control

### 1. Set Up the Virtual Environment

Python projects use virtual environments to manage dependencies. This creates an isolated space for this project's packages (like Flask), preventing them from conflicting with other Python projects on your system. It is a critical best practice for all Python development.
First, clone the repository and navigate into the project folder. Then, create the virtual environment.

```bash
# Create a new folder named 'venv' for the environment
python3 -m venv venv
# Activate the virtual environment
source venv/bin/activate
```

Your terminal prompt will now change to show `(venv)` at the beginning. This indicates that the virtual environment is active. All subsequent package installations will be contained within this environment.

### 2. Install Dependencies

Once the virtual environment is active, install all required packages from the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

### 3. Set Up Local Environment File (.env)

To run the application locally, you need to provide environment variables for configuration and secrets. This is done using a `.env` file, which should **never be committed to Git**.

1.  Create a file named `.env` in the root directory of the project.
2.  Add the following required variables. Use a strong, random string for the `FLASK_SECRET_KEY`.
    ```
    # Enables the local development login bypass
    FLASK_ENV='development'

    # A strong, random string for signing Flask sessions
    FLASK_SECRET_KEY='your_super_secret_key_here'

    # The full URL where RingCentral will redirect back to after auth
    # This must be whitelisted in your RingCentral Dev App
    RC_REDIRECT_URI='http://localhost:8080/auth/callback'

    # The base URL for the RingCentral API (Production or Sandbox)
    RC_SERVER_URL='[https://platform.ringcentral.com](https://platform.ringcentral.com)'
    ```

You also need to create a `.gitignore` file to ensure your `.env` file and other temporary files are not tracked by Git. Create a file named `.gitignore` and add the following:

```
# Environment variables
.env

# Python cache
__pycache__/
*.pyc

# Virtual environment folder
venv/
```

### 4. Run the Application

With the virtual environment active and the `.env` file created, you can now run the application.

```bash
python3 main.py
```

The application will be available at `http://localhost:8080`.

## Deployment

This application is configured for deployment to Google Cloud Run. The deployment process is automated via `cloudbuild.yaml`. Pushing changes to the connected Git repository will trigger Google Cloud Build to:

1.  Build the Docker container image using the `Dockerfile`.
2.  Push the image to Google Artifact Registry.
3.  Deploy the new image as a new revision to the Cloud Run service.
