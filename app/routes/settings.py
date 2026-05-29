from flask import Blueprint, render_template, request, redirect, url_for, flash
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
