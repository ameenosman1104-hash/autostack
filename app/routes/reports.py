from flask import Blueprint, render_template
from flask_login import login_required, current_user
from ..tenant_db import (get_stats, get_inventory_value, get_low_stock_value,
                          get_debt_aging, get_top_sold_products, get_all_products,
                          get_sales_stats, get_stock_history)

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/")
@login_required
def index():
    tid = current_user.tenant_id
    stats       = get_stats(tid)
    inv_value   = get_inventory_value(tid)
    low_value   = get_low_stock_value(tid)
    debt_aging  = get_debt_aging(tid)
    top_sold    = get_top_sold_products(tid, limit=10)
    sales_stats = get_sales_stats(tid)
    products    = get_all_products(tid)

    # Top 10 highest-value stock items
    top_value = sorted(
        [p for p in products if p["current_stock"] > 0 and p["last_cost_price"] > 0],
        key=lambda p: p["current_stock"] * p["last_cost_price"],
        reverse=True
    )[:10]

    # Category breakdown
    cat_totals = {}
    for p in products:
        cat = p["category"] or "Uncategorised"
        cat_totals[cat] = cat_totals.get(cat, 0) + p["current_stock"] * p["last_cost_price"]
    cat_totals = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

    return render_template("reports.html",
        stats=stats, inv_value=inv_value, low_value=low_value,
        debt_aging=debt_aging, top_sold=top_sold,
        sales_stats=sales_stats, top_value=top_value,
        cat_totals=cat_totals)
