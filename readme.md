# **RingCentral Australia Engineering API Tools**  
This web application provides a suite of tools for interacting with the RingCentral API, designed for support, administration, and development tasks.  
The application is built with a modular architecture to allow for easy maintenance and independent development of new features.  
  
Key Features  
-	Secure Two-Layer Authentication:
	1.	A shared passcode to unlock the website.
	2.	An OAuth 2.0 PKCE flow to securely connect to a RingCentral account.  
-	Call Flow Visualiser: An interactive tool that traces and displays the complete call routing path for any phone number in the account.  
-	Modular Tool Tabs: The application is designed to be easily extensible with new tools, each contained within its own tab. Current placeholders include:  
	-	SIP Credentials Fetcher
	-	Device Swap Utility
	-	Bulk Opening Hours Management
	-	Live Events Listener
   
# Project Structure
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

---
# Developer's Guide: Working Independently  
  
The modular design is intended to allow developers to work on features without causing conflicts. Here’s how to approach different tasks.  
  
Core Principle: Separation of Concerns  
-	The core and auth modules should rarely be touched. They handle the fundamental login and connection logic for the entire site.
-	Each tool is a self-contained unit. It has its own backend routes, frontend assets, and HTML template partial.  

---
**How to Modify an Existing Tool (Example: Call Flow Visualiser)**  
  
If you need to fix a bug or add a feature to the Call Flow Visualiser, you only need to work within its dedicated files. You can safely ignore all other modules.  
1.	Backend API Logic:
	-	Modify the API endpoints in webapp/visualiser/routes.py.
	-	Update the call flow tracing logic in webapp/visualiser/utils.py.  
2.	Frontend Interaction & Display:  
	-	Change the UI layout in webapp/templates/includes/call_flow_tab.html.
	-	Update the JavaScript that fetches data and renders the diagram in webapp/static/js/visualiser.js.
	-	Adjust the flowchart styling in webapp/static/css/visualiser.css.
  
By staying within these files, your work will not affect the SIP Fetcher, Device Swap, or any other tool.


 --- 
**How to Add a Brand New Tool (Example: "Contact Uploader")**  
  
Here is the checklist for adding a new feature to the application.  
  
Step 1: Create the Backend Module  
	-	Create a new folder: webapp/contact_uploader/.
	-	Inside it, create webapp/contact_uploader/routes.py. Define a new Flask Blueprint and add your API endpoints (e.g., /api/rc/upload-contacts).  
	  
Step 2: Create the Frontend Template
	-	Create a new HTML file: webapp/templates/includes/contact_uploader_tab.html. This file will contain the UI for your new tool (e.g., a file input and an "Upload" button).  
	  
Step 3: (If Needed) Create Frontend Assets  
	-	If your tool needs specific JavaScript or CSS, create new files:  
		-	webapp/static/js/contact_uploader.js  
		-	webapp/static/css/contact_uploader.css  
		*Important: Link these new static files directly inside your contact_uploader_tab.html file. This ensures they are only loaded when the user clicks on your tool's tab.*  
		  
Step 4: Register the New Module
	-	Open webapp/__init__.py.
	-	Import and register the new Blueprint you created in Step 1.  
	  
Step 5: Add the Tab to the Main UI
	-	Open webapp/templates/index.html.  
	-	Add your new tool to the tabs list to make it appear in the navigation bar.  
Your new tool is now integrated without having modified any of the existing tools' code.  

---
*Local Development Setup*
Prerequisites
- Python 3.10+  
- pip for package management  
  
1. Set Up Environment Variables  
	- Create a file named .env in the root directory of the project.
 	- Add the following required variables:
	- *# A strong, random string for signing Flask sessions
		- FLASK_SECRET_KEY='your_super_secret_key'
  
  	- .# The full URL where RingCentral will redirect back to after auth
	- .# For local development, this is typically:
	- .RC_REDIRECT_URI='http://localhost:8080/auth/callback'
  
 	- .# The base URL for the RingCentral API (Production or Sandbox)
	- .RC_SERVER_URL='[https://platform.ringcentral.com](https://platform.ringcentral.com)'
  
  
  
2. Install Dependencies
pip install -r requirements.txt

3. Run the Application
python main.py

The application will be available at http://localhost:8080.
Deployment
This application is configured for deployment to Google Cloud Run. The deployment process is automated via cloudbuild.yaml. Pushing changes to the connected Git repository will trigger Google Cloud Build to:
1.	Build the Docker container image using the Dockerfile.
2.	Push the image to Google Artifact Registry.
3.	Deploy the new image as a new revision to the Cloud Run service.

