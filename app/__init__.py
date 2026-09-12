from flask import Flask, send_from_directory
from flask_login import LoginManager
import os, secrets

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


def create_app():
    app = Flask(__name__)
    from .session_key import load_session_key
    app.secret_key = load_session_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    from flask_wtf.csrf import CSRFProtect
    CSRFProtect(app)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    login_manager.init_app(app)

    from .main_db import init_main_db
    init_main_db()
    from .google_auth import init_google
    init_google(app)
    from .gmail_oauth import init_gmail_oauth
    init_gmail_oauth(app)
    from .excel_oauth import init_excel_oauth
    init_excel_oauth(app)

    from .auth    import auth_bp
    from .admin   import admin_bp
    from .routes.dashboard      import dashboard_bp
    from .routes.inventory      import inventory_bp
    from .routes.purchase_orders import po_bp
    from .routes.debtors        import debtors_bp
    from .routes.settings       import settings_bp
    from .routes.notifications  import notifications_bp
    from .routes.help           import help_bp
    from .routes.reports        import reports_bp
    from .routes.recycle_bin    import recycle_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp,       url_prefix="/admin")
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inventory_bp,   url_prefix="/inventory")
    app.register_blueprint(po_bp,          url_prefix="/purchase-orders")
    app.register_blueprint(debtors_bp,     url_prefix="/debtors")
    app.register_blueprint(settings_bp,    url_prefix="/settings")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(help_bp)
    app.register_blueprint(reports_bp,     url_prefix="/reports")
    app.register_blueprint(recycle_bp,     url_prefix="/recycle-bin")

    # Expose enumerate to Jinja2 templates
    app.jinja_env.globals['enumerate'] = enumerate

    # Serve PWA files from root scope with proper headers
    @app.route('/sw.js')
    def sw():
        response = send_from_directory(app.static_folder, 'sw.js',
                                       mimetype='application/javascript')
        # Service worker should not be cached heavily (check for updates regularly)
        response.cache_control.max_age = 3600  # 1 hour
        response.cache_control.public = True
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.route('/manifest.json')
    def manifest():
        response = send_from_directory(app.static_folder, 'manifest.json',
                                       mimetype='application/manifest+json')
        # Manifest should be revalidated frequently
        response.cache_control.max_age = 3600  # 1 hour
        response.cache_control.public = True
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.after_request
    def protect_private_responses(response):
        from flask import request
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store, private"
        return response

    return app
