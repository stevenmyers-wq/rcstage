# webapp/core/routes.py
import os
from flask import (
    Blueprint, render_template, request, session, jsonify, redirect, url_for,
    make_response
)
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from webapp.auth_utils import is_authenticated
from webapp.usage_tracking import get_analytics_data

core_bp = Blueprint('core', __name__)

@core_bp.route('/')
def index():
    """Serves the main application page."""
    if os.getenv('FLASK_ENV') == 'development' and not session.get('authenticated'):
        session['authenticated'] = True
        session['user_email'] = 'developer@local.test'
        session['is_admin'] = True 
        session.modified = True
    
    rc_redirect_uri_clean = os.getenv("RC_REDIRECT_URI", "http://localhost:8080/auth/callback").rstrip('/')
    return render_template(
        'index.html', 
        AUTHENTICATED=is_authenticated(), 
        USER_ROLE='Admin' if session.get('is_admin') else 'User',
        RC_REDIRECT_URI=rc_redirect_uri_clean,
        GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID", ""),
        current_tab=request.args.get('tab', 'auth_rex')
    )

@core_bp.route('/logout')
def logout():
    response = make_response(redirect(url_for('core.index')))
    session.clear()
    response.delete_cookie('app_session') 
    return response

@core_bp.route('/api/auth/google', methods=['POST'])
def google_login():
    data = request.get_json()
    if not data or 'credential' not in data:
        return jsonify({'status': 'error', 'message': 'Missing Google credential.'}), 400
    
    token = data['credential']
    client_id = os.getenv('GOOGLE_CLIENT_ID')

    if os.getenv('FLASK_ENV') == 'development' and not client_id:
        session['authenticated'] = True
        session['user_email'] = 'developer@local.test'
        session['is_admin'] = True 
        session.modified = True
        return jsonify({'status': 'success', 'redirect_url': url_for('core.index')}), 200

    try:
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        user_email = idinfo.get('email', '').lower()
        
        if not user_email.endswith('@ringcentral.com'):
            return jsonify({'status': 'error', 'message': 'Access restricted to @ringcentral.com employees.'}), 403

        session['authenticated'] = True
        session['user_email'] = user_email
        
        admin_emails = [e.strip().lower() for e in os.getenv('ADMIN_EMAILS', '').split(',') if e.strip()]
        session['is_admin'] = user_email in admin_emails
        session.modified = True
        
        return jsonify({'status': 'success', 'redirect_url': url_for('core.index')}), 200

    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid Google token. Please try again.'}), 401


@core_bp.route('/api/auth/status')
def get_auth_status():
    return jsonify({
        'authenticated': session.get('authenticated', False), 
        'is_admin': session.get('is_admin', False), 
        'user_email': session.get('user_email', None)
    }), 200

@core_bp.route('/api/admin/analytics')
def admin_analytics():
    if not is_authenticated() or not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
    data = get_analytics_data()
    return jsonify(data), 200

# suppress favicon errors
# @core_bp.route('/favicon.ico')
# def favicon():
#    return '', 204
