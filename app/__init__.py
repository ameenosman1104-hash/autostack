from flask import Flask, send_from_directory
from flask_login import LoginManager
import os, secrets

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    login_manager.init_app(app)

    from .main_db import init_main_db
    init_main_db()

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

    # Serve PWA files from root scope
    @app.route('/sw.js')
    def sw():
        return send_from_directory(app.static_folder, 'sw.js',
                                   mimetype='application/javascript')

    @app.route('/manifest.json')
    def manifest():
        return send_from_directory(app.static_folder, 'manifest.json',
                                   mimetype='application/manifest+json')

    return app
