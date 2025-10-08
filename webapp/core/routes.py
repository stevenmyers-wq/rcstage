import os
from flask import (
    Blueprint, render_template, request, session, jsonify, redirect, url_for,
    make_response
)
from webapp.auth_utils import is_authenticated
from webapp.firestore_utils import get_config_from_firestore

# A Blueprint for core app functionality (page serving, website auth)
core_bp = Blueprint('core', __name__)

@core_bp.route('/')
def index():
    """Serves the main application page."""
    rc_redirect_uri_clean = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback").rstrip('/')
    return render_template(
        'index.html', 
        AUTHENTICATED=is_authenticated(), 
        USER_ROLE='Admin' if session.get('is_admin') else 'User',
        RC_REDIRECT_URI=rc_redirect_uri_clean,
        current_tab=request.args.get('tab', 'authenticator')
    )

@core_bp.route('/logout')
def logout():
    """Logs the user out and clears the entire session."""
    response = make_response(redirect(url_for('core.index')))
    session.clear()
    response.delete_cookie('app_session') # Use the configured cookie name
    return response

@core_bp.route('/api/auth/login', methods=['POST'])
def login():
    """Handles the website login via email and shared passcode."""
    config = get_config_from_firestore()
    if not config:
        return jsonify({'status': 'error', 'message': 'Server Error.'}), 500
        
    expected_passcode = config['passcode']
    admin_emails = config['admin_list']
    
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid format.'}), 400
    
    user_email = data.get('email', '').strip().lower()
    passcode_attempt = data.get('passcode', '').strip()

    if passcode_attempt != expected_passcode:
        return jsonify({'status': 'error', 'message': 'Invalid Passcode.'}), 401

    session['authenticated'] = True
    session['user_email'] = user_email
    session['is_admin'] = user_email in admin_emails
    session.modified = True
    
    return jsonify({'status': 'success', 'redirect_url': url_for('core.index')}), 200

@core_bp.route('/api/auth/status')
def get_auth_status():
    """API endpoint to check current website login status."""
    return jsonify({
        'authenticated': session.get('authenticated', False), 
        'is_admin': session.get('is_admin', False), 
        'user_email': session.get('user_email', None)
    }), 200