import json
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from ..tenant_db import (get_stats, get_low_stock_products, init_tenant_db, get_setting, save_setting,
                          count_overdue_debtors, count_partially_paid_debtors, get_conn, get_customer, get_all_products)

dashboard_bp = Blueprint("dashboard", __name__)

DEFAULT_SLIDES = []


def _get_todays_sales(tid):
    """Get total sales for today."""
    try:
        conn = get_conn(tid)
        today = date.today().isoformat()
        result = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE DATE(sale_date) = ? AND status='completed'",
            (today,)
        ).fetchone()
        conn.close()
        return result[0] if result else 0.0
    except:
        return 0.0


def _get_todays_transaction_count(tid):
    """Get number of completed sales today."""
    try:
        conn = get_conn(tid)
        today = date.today().isoformat()
        result = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE DATE(sale_date) = ? AND status='completed'",
            (today,)
        ).fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0


def _get_this_month_sales(tid):
    """Get total sales for this month."""
    try:
        conn = get_conn(tid)
        today = date.today()
        year_month = today.strftime("%Y-%m")
        result = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE DATE(sale_date) LIKE ? AND status='completed'",
            (f"{year_month}%",)
        ).fetchone()
        conn.close()
        return result[0] if result else 0.0
    except:
        return 0.0


def _get_recent_sales(tid, limit=5):
    """Get recent completed sales."""
    try:
        conn = get_conn(tid)
        rows = conn.execute("""
            SELECT
                i.id, i.invoice_number, i.customer_id, i.payment_method, i.total, i.sale_date
            FROM invoices i
            WHERE i.status='completed'
            ORDER BY i.sale_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        sales = []
        for row in rows:
            inv_id, inv_num, cust_id, pay_method, total, sale_date = row
            customer_name = "Walk-in"
            if cust_id:
                try:
                    cust = get_customer(tid, cust_id)
                    if cust:
                        customer_name = cust['name']
                except:
                    pass

            # Parse sale_date to extract time
            try:
                dt = datetime.fromisoformat(sale_date)
                time_str = dt.strftime("%H:%M")
            except:
                time_str = ""

            sales.append({
                'invoice_number': inv_num,
                'customer_name': customer_name,
                'payment_method': pay_method or 'cash',
                'total': total,
                'time': time_str
            })
        return sales
    except:
        return []


def _get_products_without_selling_price(tid):
    """Count products with no selling price."""
    try:
        conn = get_conn(tid)
        result = conn.execute(
            "SELECT COUNT(*) FROM products WHERE (deleted=0 OR deleted IS NULL) AND (selling_price IS NULL OR selling_price <= 0)"
        ).fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0


def _get_slides(tid):
    raw = get_setting(tid, "carousel_slides", "")
    if raw:
        try:
            slides = json.loads(raw)
            if slides:
                return slides
        except Exception:
            pass
    return DEFAULT_SLIDES


@dashboard_bp.route("/")
def index():
    # Allow unauthenticated access for PWA installability checks
    # but redirect to login if not authenticated
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    tid = current_user.tenant_id
    init_tenant_db(tid)
    stats  = get_stats(tid)
    low    = get_low_stock_products(tid)
    slides = _get_slides(tid)

    # Needs Attention metrics
    overdue_count = count_overdue_debtors(tid)
    partially_paid_count = count_partially_paid_debtors(tid)

    # POS Sales Overview metrics
    todays_sales = _get_todays_sales(tid)
    todays_transaction_count = _get_todays_transaction_count(tid)
    this_month_sales = _get_this_month_sales(tid)
    recent_sales = _get_recent_sales(tid)
    no_selling_price_count = _get_products_without_selling_price(tid)

    return render_template("dashboard.html", stats=stats, low=low, slides=slides,
                          overdue_count=overdue_count,
                          partially_paid_count=partially_paid_count,
                          todays_sales=todays_sales,
                          todays_transaction_count=todays_transaction_count,
                          this_month_sales=this_month_sales,
                          recent_sales=recent_sales,
                          no_selling_price_count=no_selling_price_count)


@dashboard_bp.route("/carousel/save", methods=["POST"])
@login_required
def save_carousel():
    tid = current_user.tenant_id
    slides = []
    for i in range(1, 6):  # support up to 5 slides
        url   = request.form.get(f"slide_url_{i}", "").strip()
        title = request.form.get(f"slide_title_{i}", "").strip()
        sub   = request.form.get(f"slide_sub_{i}", "").strip()
        if url:
            slides.append({"url": url, "title": title, "subtitle": sub})
    if not slides:
        slides = DEFAULT_SLIDES
    save_setting(tid, "carousel_slides", json.dumps(slides))
    flash("Carousel updated.", "success")
    return redirect(url_for("dashboard.index"))
