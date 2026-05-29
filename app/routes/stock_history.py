from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from ..tenant_db import get_stock_history, get_all_products

history_bp = Blueprint("history", __name__)

CHANGE_TYPE_LABELS = {
    "sale":       ("bi-cart-check",      "text-danger",  "Sale"),
    "oversell":   ("bi-exclamation-triangle", "text-danger", "OVERSOLD"),
    "manual":     ("bi-pencil",          "text-warning", "Manual Edit"),
    "sync":       ("bi-arrow-repeat",    "text-info",    "Sync"),
    "import":     ("bi-upload",          "text-primary", "Import"),
    "adjustment": ("bi-sliders",         "text-secondary","Adjustment"),
    "po_receive": ("bi-box-seam",        "text-success", "PO Received"),
}


@history_bp.route("/")
@login_required
def index():
    tid     = current_user.tenant_id
    product = request.args.get("product", "")
    ctype   = request.args.get("type", "")
    history = get_stock_history(tid, limit=500)

    if product:
        history = [h for h in history if product.lower() in h["product_name"].lower()]
    if ctype:
        history = [h for h in history if h["change_type"] == ctype]

    products  = get_all_products(tid)
    # Pull all oversell entries (unfiltered) for the alert banner
    all_history = get_stock_history(tid, limit=500) if (product or ctype) else history
    alerts = [h for h in all_history if h["change_type"] == "oversell"]
    return render_template("stock_history.html", history=history,
                           products=products, labels=CHANGE_TYPE_LABELS,
                           filter_product=product, filter_type=ctype,
                           alerts=alerts)
