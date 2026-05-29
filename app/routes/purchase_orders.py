from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..tenant_db import get_low_stock_products, get_all_products

po_bp = Blueprint("po", __name__)


@po_bp.route("/")
@login_required
def index():
    tid  = current_user.tenant_id
    items = get_low_stock_products(tid)
    for item in items:
        suggested = max(1.0, item["reorder_level"] * 2 - item["current_stock"])
        item["suggested_qty"] = suggested
        item["est_total"]     = suggested * item["previous_cost_price"]
        item["status"]        = "OUT OF STOCK" if item["current_stock"] <= 0 else "LOW STOCK"
    return render_template("purchase_orders.html", items=items)
