``` /your-project-name
|
├── main.py              # New entry point to run the app
|
├── requirements.txt     # (Your existing file)
|
└── /webapp              # Your main application package
    |
    ├── __init__.py      # The application factory
    |
    ├── utils.py         # All your helper functions
    |
    ├── /templates/      # Your HTML files
    │   ├── error.html
    │   └── index.html
    │   └── /includes/   # Your template partials
    │       ├── authenticator_tab.html
    │       ├── bulk_opening_tab.html
    │       ├── call_flow_tab.html
    │       ├── device_swap_tab.html
    │       ├── live_events_tab.html
    │       └── sip_fetcher_tab.html
    |
    ├── /core/
    │   └── routes.py    # Core routes like index, login, logout, etc.
    |
    └── /visualiser/
        └── routes.py    # Routes for the call flow visualiser
```
        
        



Now that your project has this modular structure, adding new features is a simple and repeatable process.

For each new feature on your website (like "Device Swap," "Live Events," etc.), you will simply repeat these three steps.

Let's use Device Swap as an example.

## Step 1: Create the Feature Folder and Files
In your webapp folder, create a new folder for your feature. Inside that folder, create an empty __init__.py file and a routes.py file.

Your structure will now look like this:

``` /webapp
|
├── /core/
│   └── routes.py
|
├── /device_swap/        <-- NEW FOLDER
│   ├── __init__.py      <-- NEW (empty file)
│   └── routes.py        <-- NEW FILE
|
└── /visualiser/
    └── routes.py
```
    
## Step 2: Define the Blueprint in routes.py
Open your new webapp/device_swap/routes.py file. Here, you'll define a new Blueprint and add the routes specific to the Device Swap feature.

A great practice is to use a url_prefix. This automatically adds a prefix to all routes in this file, keeping your URLs organized.

File: webapp/device_swap/routes.py

Python

from flask import Blueprint, jsonify, request
# Import any utility functions you need for this feature
from webapp.utils import is_authenticated, get_rc_access_token, rc_api_call

# 1. Define the Blueprint with a URL prefix
device_swap_bp = Blueprint(
    'device_swap', 
    __name__, 
    url_prefix='/api/rc/device-swap'
)

# 2. Add your routes for this feature
@device_swap_bp.route('/execute', methods=['POST'])
def execute_swap():
    # First, always check for authentication
    if not is_authenticated():
        return jsonify({'status': 'error', 'message': 'Website not unlocked.'}), 401
    if not get_rc_access_token():
        return jsonify({'status': 'error', 'message': 'RingCentral not connected.'}), 401

    # Get device IDs from the request
    data = request.get_json()
    source_device_id = data.get('sourceDeviceId')
    target_device_id = data.get('targetDeviceId')

    # --- ADD YOUR DEVICE SWAP LOGIC HERE ---
    # This is where you would make the series of rc_api_call()s 
    # to perform the swap.
    # ---

    # For now, we can return a placeholder success message
    return jsonify({
        'status': 'success', 
        'message': f'Device swap initiated between {source_device_id} and {target_device_id}.'
    })

## Step 3: Register the New Blueprint
Finally, "plug in" your new feature by registering its Blueprint in the main application factory.

Open webapp/__init__.py.

Add the two lines to import and register your new device_swap_bp.

File: webapp/__init__.py

Python

import os
from flask import Flask
from dotenv import load_dotenv

def create_app():
    # ... (Flask app setup code as before) ...
    
    # --- Register Blueprints ---
    with app.app_context():
        # Core routes
        from .core import routes as core_routes
        app.register_blueprint(core_routes.core_bp)

        # Visualiser routes
        from .visualiser import routes as visualiser_routes
        app.register_blueprint(visualiser_routes.viz_bp)
        
        # ADD THESE TWO LINES FOR YOUR NEW FEATURE
        from .device_swap import routes as device_swap_routes
        app.register_blueprint(device_swap_routes.device_swap_bp)

    return app
That's it! You have successfully added a new, isolated feature to your application.

You can now follow this exact same Create Folder -> Define Blueprint -> Register Blueprint pattern for all your other features like Live Events, SIP Fetcher, and Bulk Opening Hours. 
