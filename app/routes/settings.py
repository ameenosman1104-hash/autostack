from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..tenant_db import get_all_settings, save_setting

settings_bp = Blueprint("settings", __name__)

ALL_KEYS = [
    "business_name",
    "notification_method",
    "email_smtp_host",
    "email_smtp_port",
    "email_sender",
    "email_password",
    "email_recipients",
    "whatsapp_number",
    "callmebot_api_key",
    "notification_message",
    "debt_reminder_template",
    "default_reminder_days",
]


@settings_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    tid = current_user.tenant_id
    if request.method == "POST":
        for key in ALL_KEYS:
            save_setting(tid, key, request.form.get(key, ""))
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))
    settings = get_all_settings(tid)
    return render_template("settings.html", s=settings)


@settings_bp.route("/test-email", methods=["POST"])
@login_required
def test_email():
    """Test email connection with provided credentials."""
    tid = current_user.tenant_id
    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip()
    password = data.get("password", "").strip()

    if not email:
        return jsonify(ok=False, msg="Email address required")
    if not password:
        return jsonify(ok=False, msg="App password required")

    try:
        import smtplib
        smtp_host = get_all_settings(tid).get("email_smtp_host", "smtp.gmail.com")
        smtp_port = int(get_all_settings(tid).get("email_smtp_port", "587"))

        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        server.starttls()
        server.login(email, password)
        server.quit()

        return jsonify(ok=True, msg="Email connection successful!")

    except smtplib.SMTPAuthenticationError:
        return jsonify(ok=False, msg="Authentication failed. Check your App Password (16 characters).")
    except smtplib.SMTPException as e:
        error_str = str(e)
        if "Connection refused" in error_str:
            return jsonify(ok=False, msg="Cannot connect to Gmail. Check internet connection.")
        return jsonify(ok=False, msg=f"SMTP error: {error_str}")
    except Exception as e:
        return jsonify(ok=False, msg=f"Error: {str(e)}")
