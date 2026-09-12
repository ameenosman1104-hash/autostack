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
    "reminders_enabled",
]


@settings_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    tid = current_user.tenant_id
    if request.method == "POST":
        from ..validation import reminder_interval
        try:
            reminder_interval(request.form.get("default_reminder_days", "28"))
        except ValueError:
            flash("Reminder interval must be between 1 and 365 days.", "danger")
            return redirect(url_for("settings.index"))
        for key in ALL_KEYS:
            if key not in request.form:
                continue
            value = request.form.get(key, "")
            if key == "email_password":
                value = value.strip()
                if not value or set(value) <= set("*•●"):
                    continue
                value = value.replace(" ", "")
            save_setting(tid, key, value)
            # Log (without exposing password)
            if key == "email_password" and value:
                print(f"[SETTINGS] Saved {key} (length: {len(value)} chars)")
            elif key == "email_sender" and value:
                print(f"[SETTINGS] Saved {key}: {value}")
        from ..tenant_db import get_all_debtors, calculate_next_reminder
        for debtor in get_all_debtors(tid):
            if debtor.get("reminder_mode", "default") == "default":
                try:
                    calculate_next_reminder(tid, debtor["id"])
                except (ValueError, TypeError):
                    # Invalid date_of_purchase (None or unparseable) should not crash settings save
                    flash("A debtor has an invalid purchase date; its reminder was not changed.", "warning")
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))
    settings = get_all_settings(tid)
    print(f"[SETTINGS] Loaded settings for tenant {tid}: email_sender={settings.get('email_sender', 'NOT FOUND')}")
    return render_template("settings.html", s=settings)


@settings_bp.route("/test-email", methods=["POST"])
@login_required
def test_email():
    """Test email connection with provided credentials."""
    tid = current_user.tenant_id
    data = request.get_json(silent=True) or {}

    email_from_form = data.get("email", "").strip()
    password_from_form = data.get("password", "").strip()

    # Load saved settings
    settings = get_all_settings(tid)
    email_from_db = settings.get("email_sender", "").strip()
    password_from_db = settings.get("email_password", "").strip()

    # Use form values if provided, otherwise fall back to database
    email = email_from_form or email_from_db
    password = password_from_form or password_from_db

    # Diagnostic logging (NO PASSWORD LOGGING)
    print(f"[TEST-EMAIL] Form email: {email_from_form}, DB email: {email_from_db}, Using: {email}")
    print(f"[TEST-EMAIL] Form pass provided: {bool(password_from_form)}, DB pass exists: {bool(password_from_db)}, Using pass: {bool(password)}")

    if not email:
        print(f"[TEST-EMAIL] FAIL: No email configured")
        return jsonify(ok=False, msg="No sender email found. Save your email address in Settings > Email Settings.")
    if not password:
        print(f"[TEST-EMAIL] FAIL: No password configured")
        return jsonify(ok=False, msg="No Gmail App Password found. Save your App Password in Settings > Email Settings.")

    try:
        import smtplib
        import ssl
        smtp_host = settings.get("email_smtp_host", "smtp.gmail.com")
        smtp_port = int(settings.get("email_smtp_port", "587"))

        print(f"[TEST-EMAIL] Connecting to {smtp_host}:{smtp_port}")
        # Create secure SSL context that verifies certificates
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        server.starttls(context=context)
        print(f"[TEST-EMAIL] Authenticating as {email}")
        server.login(email, password)
        server.quit()

        print(f"[TEST-EMAIL] SUCCESS")
        return jsonify(ok=True, msg="Email connection successful!")

    except smtplib.SMTPAuthenticationError as e:
        print(f"[TEST-EMAIL] Auth error: {e}")
        return jsonify(ok=False, msg="Authentication failed. Check your App Password (16 characters).")
    except smtplib.SMTPException as e:
        error_str = str(e)
        print(f"[TEST-EMAIL] SMTP error: {error_str}")
        if "Connection refused" in error_str:
            return jsonify(ok=False, msg="Cannot connect to Gmail. Check internet connection.")
        return jsonify(ok=False, msg=f"SMTP error: {error_str}")
    except Exception as e:
        print(f"[TEST-EMAIL] Exception: {e}")
        return jsonify(ok=False, msg=f"Error: {str(e)}")


@settings_bp.route("/send-reminders", methods=["POST"])
@login_required
def send_reminders():
    """Manually trigger reminder dispatch for this business."""
    tid = current_user.tenant_id

    try:
        from ..reminder_scheduler import check_and_send_reminders
        stats = check_and_send_reminders(tid)

        if isinstance(stats.get("skipped"), str):
            return jsonify(ok=False, msg=stats["skipped"])

        message = f"Sent {stats['sent']} reminder(s)"
        if stats["failed"] > 0:
            message += f", {stats['failed']} failed"
        if stats["errors"]:
            message += f"\nErrors: {'; '.join(stats['errors'][:3])}"

        return jsonify(ok=True, msg=message, stats=stats)

    except Exception as e:
        return jsonify(ok=False, msg=f"Error sending reminders: {str(e)}")
