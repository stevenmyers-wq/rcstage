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
        from .custom_rules import routes as custom_rules_routes
        app.register_blueprint(custom_rules_routes.custom_rules_bp)

        # Extension Renamer routes
        from .extension_renamer import routes as extension_renamer_routes
        app.register_blueprint(extension_renamer_routes.renamer_bp)

        # Notifications Manager routes
        from .notifications import routes as notifications_routes
        app.register_blueprint(notifications_routes.notifications_bp)

        # Greetings Uploader routes
        from .greetings_uploader import routes as greetings_uploader_routes
        app.register_blueprint(greetings_uploader_routes.greetings_uploader_bp)

        # RingEX UAT Generator routes
        from .ringex_uat import routes as ringex_uat_routes
        app.register_blueprint(ringex_uat_routes.ringex_uat_bp)
        
        # AI Demo Calls routes
        from .ai_demo_calls import routes as ai_demo_calls_routes
        app.register_blueprint(ai_demo_calls_routes.ai_demo_calls_bp)

        # Business Analytics routes
        from .analytics import routes as analytics_routes
        app.register_blueprint(analytics_routes.analytics_bp)

        # BLF & Presence routes
        from .presence import routes as presence_routes
        app.register_blueprint(presence_routes.presence_bp)
        
        # Account Discovery routes
        from .account_health import routes as account_health_routes
        app.register_blueprint(account_health_routes.account_health_bp)
        
        # CXone Script Analyzer routes
        from .cxone_script_analyzer import routes as cxone_script_analyzer_routes
        app.register_blueprint(cxone_script_analyzer_routes.cxone_script_analyzer_bp)

        # Register the new CXone Audio Converter Blueprint
        from .cxone_audio_converter import routes as cxone_audio_converter_routes
        app.register_blueprint(cxone_audio_converter_routes.cxone_audio_converter_bp)

    return app
