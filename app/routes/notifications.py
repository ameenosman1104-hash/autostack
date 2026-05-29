from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from ..tenant_db import get_notification_log, get_low_stock_products, get_setting
from ..tenant_db import log_notification

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/")
@login_required
def index():
    logs = get_notification_log(current_user.tenant_id)
    return render_template("notifications.html", logs=logs)


@notifications_bp.route("/send-alert", methods=["POST"])
@login_required
def send_alert():
    tid   = current_user.tenant_id
    low   = get_low_stock_products(tid)
    if not low:
        return jsonify(ok=False, msg="All stock levels are OK — no alert needed.")

    from ..services.notifier import send_low_stock_alert
    ok, msg = send_low_stock_alert(tid, low)
    log_notification(tid, "inventory_alert", "", f"{len(low)} items", "SUCCESS" if ok else f"FAILED: {msg}")
    return jsonify(ok=ok, msg=msg)
