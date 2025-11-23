# webapp/__init__.py
import os
from flask import Flask
from dotenv import load_dotenv

def create_app():
    # Load environment variables in non-production environments
    if os.environ.get("FLASK_ENV") != "production":
        load_dotenv()

    app = Flask(__name__)

    # --- CONFIGURATION ---
    app.secret_key = os.getenv("FLASK_SECRET_KEY")
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['RC_SERVER_URL'] = os.getenv("RC_SERVER_URL", "https://platform.ringcentral.com")

    # --- Register Blueprints ---
    with app.app_context():
        # Core routes (index, login, logout)
        from .core import routes as core_routes
        app.register_blueprint(core_routes.core_bp)

        # RingCentral PKCE authentication routes
        from .auth import routes as auth_routes
        app.register_blueprint(auth_routes.auth_bp)

        # Visualiser routes (API calls for the call flow feature)
        from .visualiser import routes as visualiser_routes
        app.register_blueprint(visualiser_routes.viz_bp)
        
        # SIP Fetcher routes
        from .sip_fetcher import routes as sip_fetcher_routes
        app.register_blueprint(sip_fetcher_routes.sip_fetcher_bp)
        
        # Bulk Hours Tool routes
        from .bulk_hours import routes as bulk_hours_routes
        app.register_blueprint(bulk_hours_routes.bulk_hours_bp)
        
        # Personal Address Book routes
        from .personal_address_book import routes as personal_address_book_routes
        app.register_blueprint(personal_address_book_routes.personal_address_book_bp)
        
        # Live Events routes
        from .live_events import routes as live_events_routes
        app.register_blueprint(live_events_routes.live_events_bp)

        # Custom Rule routes
        from webapp.custom_rules.routes import custom_rules_bp
        app.register_blueprint(custom_rules_bp)

        # (When you add another new feature, you'll import and register its blueprint here)

    return app
