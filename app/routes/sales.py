from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..tenant_db import (get_all_products, get_product, get_all_sales, add_sale,
                          delete_sale, get_sales_stats, update_product,
                          log_stock_change, auto_create_po_if_needed)

sales_bp = Blueprint("sales", __name__)


@sales_bp.route("/")
@login_required
def index():
    tid   = current_user.tenant_id
    sales = get_all_sales(tid)
    stats = get_sales_stats(tid)
    return render_template("sales.html", sales=sales, stats=stats)


@sales_bp.route("/record", methods=["POST"])
@login_required
def record():
    tid  = current_user.tenant_id
    data = request.get_json(silent=True) or {}
    pid  = int(data.get("product_id", 0))
    qty  = float(data.get("qty_sold", 0))
    price = float(data.get("sale_price", 0))
    notes = data.get("notes", "").strip()

    if not pid or qty <= 0:
        return jsonify(ok=False, msg="Invalid product or quantity.")

    product = get_product(tid, pid)
    if not product:
        return jsonify(ok=False, msg="Product not found.")

    qty_before = product["current_stock"]
    oversold   = qty > qty_before          # selling more than available
    shortfall  = qty - qty_before if oversold else 0
    new_stock  = max(0, qty_before - qty)

    # Deduct stock
    update_product(tid, pid, current_stock=new_stock)

    # Log the sale
    add_sale(tid, pid, product["code"], product["name"], qty, price, notes)

    # Write audit trail — flag oversell with a special change_type
    change_type = "oversell" if oversold else "sale"
    note = f"Sale: {qty} unit(s) @ R{price:.2f}"
    if oversold:
        note += f" — OVERSOLD by {shortfall} unit(s) (only {qty_before} in stock)"
    if notes:
        note += f" — {notes}"
    log_stock_change(tid, pid, product["code"], product["name"],
                     change_type, qty_before, new_stock, note)

    # Send alert email if oversold
    if oversold:
        try:
            from ..services.notifier import _via_email, _build_message
            from ..tenant_db import get_setting
            alert_msg = (
                f"OVERSTOCK ALERT\n\n"
                f"Product : {product['name']} ({product['code']})\n"
                f"In Stock : {qty_before}\n"
                f"Qty Sold : {qty}\n"
                f"Shortfall: {shortfall} unit(s)\n\n"
                f"Stock has been set to 0. Please verify physical stock immediately."
            )
            _via_email(tid, alert_msg)
        except Exception:
            pass  # don't fail the sale if email fails

    # Auto-create a draft PO if stock just dropped below minimum
    auto_po = None
    if product.get("reorder_level", 0) > 0 and new_stock <= product["reorder_level"]:
        auto_po = auto_create_po_if_needed(tid)

    msg = f"Recorded: {qty} × {product['name']} sold. Stock now {new_stock}."
    if oversold:
        msg += f" ⚠ WARNING: oversold by {shortfall} unit(s) — alert sent."
    if auto_po:
        msg += f" Auto-PO {auto_po[1]} drafted."

    return jsonify(ok=True, new_stock=new_stock, oversold=oversold,
                   auto_po=bool(auto_po), msg=msg)


@sales_bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    delete_sale(current_user.tenant_id, sid)
    flash("Sale record deleted.", "success")
    return redirect(url_for("sales.index"))
