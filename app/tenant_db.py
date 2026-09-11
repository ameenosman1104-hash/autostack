"""Per-tenant SQLite database — same schema as the desktop app."""
import sqlite3, os
from datetime import datetime, date, timedelta

DATA_DIR = os.path.join(os.environ.get("AUTOSTACK_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")), "tenants")


def _db_path(tenant_id):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        test_path = os.path.join(DATA_DIR, f"{tenant_id}.db")
        sqlite3.connect(test_path).close()
        return test_path
    except:
        return f":memory:?cache=shared&uri=file:tenant_{tenant_id}"


def get_conn(tenant_id):
    db_path = _db_path(tenant_id)
    try:
        # Only apply migrations to file-based databases, not :memory:
        if not db_path.startswith(":memory:"):
            from app.db_migrations import migrate_tenant_db
            try:
                migrate_tenant_db(tenant_id, db_path)
            except RuntimeError as e:
                print(f"[WARNING] Migration issue: {e}")
                raise

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    except Exception as e:
        print(f"Failed to connect to database {db_path}: {e}")
        raise


def init_tenant_db(tenant_id):
    """Initialize database schema for tenant. Migrations are now handled by db_migrations."""
    try:
        conn = get_conn(tenant_id)
        conn.close()
    except Exception as e:
        print(f"init_tenant_db error: {e}")
        raise


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(tid, key, default=""):
    conn = get_conn(tid)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def save_setting(tid, key, value):
    conn = get_conn(tid)
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_all_settings(tid):
    conn = get_conn(tid)
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ── Products ──────────────────────────────────────────────────────────────────

def get_all_products(tid, search=""):
    conn = get_conn(tid)
    if search:
        q = f"%{search}%"
        rows = conn.execute(
            "SELECT * FROM products WHERE (deleted=0 OR deleted IS NULL) AND (name LIKE ? OR code LIKE ? OR category LIKE ?) ORDER BY name",
            (q, q, q)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM products WHERE (deleted=0 OR deleted IS NULL) ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_deleted_products(tid):
    conn = get_conn(tid)
    rows = conn.execute("SELECT * FROM products WHERE deleted=1 ORDER BY deleted_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_low_stock_products(tid):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM products WHERE (deleted=0 OR deleted IS NULL) AND reorder_level > 0 AND current_stock <= reorder_level ORDER BY current_stock ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product(tid, pid):
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM products WHERE id=? AND (deleted=0 OR deleted IS NULL)", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_product_by_code(tid, code):
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM products WHERE LOWER(code)=LOWER(?) AND (deleted=0 OR deleted IS NULL)", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_product(tid, code, name, category="", unit="PCS", current_stock=0,
                reorder_level=0, last_cost_price=0, supplier="", extra_data="{}"):
    conn = get_conn(tid)
    try:
        cur = conn.execute(
            """INSERT INTO products (code,name,category,unit,current_stock,reorder_level,
               last_cost_price,previous_cost_price,supplier,extra_data) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (code, name, category, unit, current_stock, reorder_level,
             last_cost_price, last_cost_price, supplier, extra_data)
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_product(tid, pid, **kwargs):
    conn = get_conn(tid)
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [pid]
    conn.execute(f"UPDATE products SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def count_products_with_manual_min(tid):
    """Returns count of products whose min level was set manually from the table."""
    conn = get_conn(tid)
    n = conn.execute("SELECT COUNT(*) FROM products WHERE min_level_manual=1").fetchone()[0]
    conn.close()
    return n


def set_default_min_level(tid, min_level, override_manual=True):
    """Set reorder_level for all products. If override_manual=False, skip manually-set ones."""
    conn = get_conn(tid)
    if override_manual:
        conn.execute("UPDATE products SET reorder_level=?, min_level_manual=0", (min_level,))
    else:
        conn.execute("UPDATE products SET reorder_level=? WHERE min_level_manual=0", (min_level,))
    conn.commit()
    conn.close()


def delete_product(tid, pid):
    conn = get_conn(tid)
    conn.execute("UPDATE products SET deleted=1, deleted_at=datetime('now') WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def delete_all_products(tid):
    conn = get_conn(tid)
    conn.execute("UPDATE products SET deleted=1, deleted_at=datetime('now') WHERE (deleted=0 OR deleted IS NULL)")
    conn.commit()
    conn.close()


def delete_products_by_ids(tid, ids):
    if not ids:
        return 0
    conn = get_conn(tid)
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE products SET deleted=1, deleted_at=datetime('now') WHERE id IN ({placeholders})", ids
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def restore_product(tid, pid):
    conn = get_conn(tid)
    conn.execute("UPDATE products SET deleted=0, deleted_at=NULL WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def permanent_delete_product(tid, pid):
    conn = get_conn(tid)
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def permanent_delete_all_deleted(tid):
    conn = get_conn(tid)
    conn.execute("DELETE FROM products WHERE deleted=1")
    conn.commit()
    conn.close()


def get_stats(tid):
    try:
        conn = get_conn(tid)
        f = "(deleted=0 OR deleted IS NULL)"
        total = conn.execute(f"SELECT COUNT(*) FROM products WHERE {f}").fetchone()[0]
        low   = conn.execute(
            f"SELECT COUNT(*) FROM products WHERE {f} AND reorder_level > 0 AND current_stock <= reorder_level AND current_stock > 0"
        ).fetchone()[0]
        out   = conn.execute(f"SELECT COUNT(*) FROM products WHERE {f} AND current_stock <= 0").fetchone()[0]
        debtors = conn.execute("SELECT COUNT(*) FROM debtors WHERE is_paid=0").fetchone()[0]
        owed    = conn.execute("SELECT COALESCE(SUM(MAX(0,amount_owed-COALESCE(total_paid_to_date,0))),0) FROM debtors WHERE is_paid=0").fetchone()[0]
        conn.close()
        return {"total": total, "low_stock": low, "out_of_stock": out,
                "active_debtors": debtors, "total_owed": owed}
    except:
        return {"total": 0, "low_stock": 0, "out_of_stock": 0, "active_debtors": 0, "total_owed": 0}


# ── Debtors ───────────────────────────────────────────────────────────────────

def outstanding_balance(debtor):
    """Legacy amount_owed remains the original debt; never rewrite historical amounts."""
    from decimal import Decimal
    if debtor.get("is_paid"):
        return 0.0
    return float(max(Decimal("0"), Decimal(str(debtor.get("amount_owed") or 0)) - Decimal(str(debtor.get("total_paid_to_date") or 0))))


def get_all_debtors(tid, show_paid=False):
    conn = get_conn(tid)
    if show_paid:
        rows = conn.execute("SELECT * FROM debtors ORDER BY is_paid ASC, name ASC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM debtors WHERE is_paid=0 ORDER BY name ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_debtor(tid, did):
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM debtors WHERE id=?", (did,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_debtor_by_name(tid, name):
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM debtors WHERE LOWER(name)=LOWER(?) AND is_paid=0", (name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_debtor(tid, name, phone, email, amount_owed, date_of_purchase,
               notify_method, reminder_days, notes, products_owed=""):
    conn = get_conn(tid)
    conn.execute(
        """INSERT INTO debtors (name,phone,email,amount_owed,date_of_purchase,
           notify_method,reminder_days,notes,products_owed) VALUES (?,?,?,?,?,?,?,?,?)""",
        (name, phone, email, amount_owed, date_of_purchase,
         notify_method, reminder_days, notes, products_owed)
    )
    conn.commit()
    conn.close()


def update_debtor(tid, did, **kwargs):
    conn = get_conn(tid)
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [did]
    conn.execute(f"UPDATE debtors SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def calculate_next_reminder(tid, did):
    """Keep the purchase-date anchor; advance only after a recorded successful send."""
    from .validation import reminder_interval
    debtor = get_debtor(tid, did)
    if not debtor:
        return None
    if debtor.get("reminder_mode") in ("manual", "custom"):
        return debtor.get("next_reminder_date")
    days = reminder_interval(debtor.get("reminder_interval_days", 28) if debtor.get("reminder_mode") == "custom_interval" else get_setting(tid,"default_reminder_days","28"))
    base = date.fromisoformat(debtor["date_of_purchase"])
    next_date = base + timedelta(days=days)
    if debtor.get("last_reminded"):
        last = date.fromisoformat(debtor["last_reminded"][:10])
        if last >= next_date:
            next_date = base + timedelta(days=((last-base).days // days + 1) * days)
    value = next_date.isoformat()
    update_debtor(tid,did,next_reminder_date=value)
    return value


def set_custom_interval_reminder(tid, did, interval_days):
    """Set a custom interval reminder for a debtor (calculates from purchase date).

    Args:
        tid: Tenant ID
        did: Debtor ID
        interval_days: Interval in days (e.g., 7, 14, 21, 28, 42, 56)

    Returns:
        The calculated next reminder date, or None if error
    """
    try:
        debtor = get_debtor(tid, did)
        if not debtor:
            return None

        from .validation import reminder_interval
        interval_days = reminder_interval(interval_days)
        purchase_date = datetime.strptime(debtor["date_of_purchase"], "%Y-%m-%d").date()
        next_date = purchase_date + timedelta(days=int(interval_days))
        next_date_str = next_date.isoformat()

        # Update debtor with custom interval mode
        update_debtor(tid, did,
                     reminder_mode="custom_interval",
                     next_reminder_date=next_date_str,
                     reminder_interval_days=int(interval_days))

        return next_date_str
    except Exception as e:
        print(f"Error setting custom interval reminder: {e}")
        return None


def set_default_reminder_mode(tid, did):
    """Set a debtor back to DEFAULT mode (recalculates from purchase date).

    Args:
        tid: Tenant ID
        did: Debtor ID

    Returns:
        The calculated next reminder date, or None if error
    """
    try:
        # Update to default mode
        update_debtor(tid, did, reminder_mode="default")

        # Recalculate the date
        return calculate_next_reminder(tid, did)
    except Exception as e:
        print(f"Error setting default reminder mode: {e}")
        return None


def delete_debtor(tid, did):
    conn = get_conn(tid)
    conn.execute("DELETE FROM debtors WHERE id=?", (did,))
    conn.commit()
    conn.close()


def delete_all_debtors(tid):
    conn = get_conn(tid)
    conn.execute("DELETE FROM debtors")
    conn.commit()
    conn.close()


def delete_debtors_by_ids(tid, ids):
    if not ids:
        return 0
    conn = get_conn(tid)
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM debtors WHERE id IN ({placeholders})", ids)
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def next_reminder(debtor):
    """Calculate reminder status from stored next_reminder_date.

    CRITICAL: Uses the stored next_reminder_date which is either:
    - Calculated (for DEFAULT mode): purchase_date + global_default_interval
    - Stored (for CUSTOM mode): user-selected custom interval or date

    Never recalculates; always uses database value.
    """
    try:
        # Use stored next_reminder_date directly (respects both DEFAULT and CUSTOM modes)
        if debtor.get("next_reminder_date"):
            nxt = datetime.strptime(debtor["next_reminder_date"], "%Y-%m-%d").date()
        else:
            # Fallback for legacy debtors without next_reminder_date set
            base_str = debtor["last_reminded"] if debtor.get("last_reminded") else debtor["date_of_purchase"]
            base = datetime.strptime(base_str, "%Y-%m-%d").date()
            nxt = base + timedelta(days=int(debtor.get("reminder_days", 14)))

        today = date.today()
        if nxt < today:
            return nxt.strftime("%d %b %Y"), "overdue"
        elif nxt == today:
            return "Today", "due_today"
        else:
            return nxt.strftime("%d %b %Y"), "ok"
    except Exception as e:
        print(f"Error calculating next_reminder: {e}")
        return "—", "ok"


# ── Notifications ─────────────────────────────────────────────────────────────

def log_notification(tid, ntype, recipient, message, status):
    conn = get_conn(tid)
    conn.execute(
        "INSERT INTO notification_log (notification_type,recipient,message,status) VALUES (?,?,?,?)",
        (ntype, recipient, message[:1000], status)
    )
    conn.commit()
    conn.close()


def get_notification_log(tid, limit=100):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM notification_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Sales ─────────────────────────────────────────────────────────────────────

def add_sale(tid, product_id, product_code, product_name, qty_sold,
             sale_price=0, notes=""):
    total = qty_sold * sale_price
    conn = get_conn(tid)
    conn.execute(
        """INSERT INTO sales (product_id,product_code,product_name,qty_sold,
           sale_price,total_amount,notes) VALUES (?,?,?,?,?,?,?)""",
        (product_id, product_code, product_name, qty_sold, sale_price, total, notes)
    )
    conn.commit()
    conn.close()


def get_all_sales(tid, limit=200):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM sales ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sales_by_product(tid, product_id):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM sales WHERE product_id=? ORDER BY created_at DESC", (product_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sales_stats(tid):
    conn = get_conn(tid)
    total_rev  = conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM sales").fetchone()[0]
    total_qty  = conn.execute("SELECT COALESCE(SUM(qty_sold),0) FROM sales").fetchone()[0]
    total_txns = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    today_rev  = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM sales WHERE date(created_at)=date('now')"
    ).fetchone()[0]
    conn.close()
    return {"total_revenue": total_rev, "total_qty": total_qty,
            "total_transactions": total_txns, "today_revenue": today_rev}


def delete_sale(tid, sid):
    conn = get_conn(tid)
    conn.execute("DELETE FROM sales WHERE id=?", (sid,))
    conn.commit()
    conn.close()


# ── Stock History ─────────────────────────────────────────────────────────────

def log_stock_change(tid, product_id, product_code, product_name,
                     change_type, qty_before, qty_after, notes=""):
    change_by = qty_after - qty_before
    conn = get_conn(tid)
    conn.execute(
        """INSERT INTO stock_history (product_id,product_code,product_name,
           change_type,qty_before,qty_after,change_by,notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (product_id, product_code, product_name,
         change_type, qty_before, qty_after, change_by, notes)
    )
    conn.commit()
    conn.close()


def get_stock_history(tid, limit=300):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM stock_history ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_history(tid, product_id, limit=50):
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM stock_history WHERE product_id=? ORDER BY created_at DESC LIMIT ?",
        (product_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Suppliers ─────────────────────────────────────────────────────────────────

def get_all_suppliers(tid):
    conn = get_conn(tid)
    rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_supplier(tid, sid):
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_supplier(tid, name, phone="", email="", address="",
                 lead_time_days=0, payment_terms="", notes=""):
    conn = get_conn(tid)
    cur = conn.execute(
        """INSERT INTO suppliers (name,phone,email,address,lead_time_days,
           payment_terms,notes) VALUES (?,?,?,?,?,?,?)""",
        (name, phone, email, address, lead_time_days, payment_terms, notes)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_supplier(tid, sid, **kwargs):
    conn = get_conn(tid)
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [sid]
    conn.execute(f"UPDATE suppliers SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_supplier(tid, sid):
    conn = get_conn(tid)
    conn.execute("DELETE FROM suppliers WHERE id=?", (sid,))
    conn.commit()
    conn.close()


# ── Reports helpers ───────────────────────────────────────────────────────────

def get_inventory_value(tid):
    """Total value of current stock at cost price."""
    conn = get_conn(tid)
    val = conn.execute(
        "SELECT COALESCE(SUM(current_stock * last_cost_price),0) FROM products"
    ).fetchone()[0]
    conn.close()
    return val


def get_debt_aging(tid):
    """Buckets: current, 30, 60, 90+ days overdue."""
    from datetime import date, timedelta
    conn  = get_conn(tid)
    rows  = conn.execute(
        "SELECT MAX(0,amount_owed-COALESCE(total_paid_to_date,0)) AS amount_owed, date_of_purchase FROM debtors WHERE is_paid=0"
    ).fetchall()
    conn.close()
    today = date.today()
    buckets = {"current": 0, "30": 0, "60": 0, "90plus": 0}
    for r in rows:
        try:
            age = (today - date.fromisoformat(r["date_of_purchase"])).days
        except Exception:
            age = 0
        amt = r["amount_owed"]
        if age <= 30:   buckets["current"] += amt
        elif age <= 60: buckets["30"]      += amt
        elif age <= 90: buckets["60"]      += amt
        else:           buckets["90plus"]  += amt
    return buckets


def get_top_sold_products(tid, limit=10):
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT product_name, product_code,
           SUM(qty_sold) as total_qty, SUM(total_amount) as total_revenue
           FROM sales GROUP BY product_id ORDER BY total_qty DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_low_stock_value(tid):
    conn = get_conn(tid)
    val = conn.execute(
        """SELECT COALESCE(SUM(current_stock * last_cost_price),0) FROM products
           WHERE reorder_level > 0 AND current_stock <= reorder_level"""
    ).fetchone()[0]
    conn.close()
    return val


# ── Auto Purchase Order ───────────────────────────────────────────────────────

def auto_create_po_if_needed(tid, triggered_products=None):
    """
    Check for products below min level that have no open DRAFT PO covering them.
    If found, creates a new DRAFT PO and returns (po_id, po_number, count) or None.
    triggered_products: list of product dicts that just changed (to limit scope).
    """
    conn = get_conn(tid)

    # Find all products currently below min with no open PO line for them
    low = conn.execute(
        """SELECT p.* FROM products p
           WHERE p.reorder_level > 0 AND p.current_stock <= p.reorder_level
             AND p.code NOT IN (
               SELECT poi.product_code FROM purchase_order_items poi
               JOIN purchase_orders po ON po.id = poi.po_id
               WHERE po.status = 'DRAFT'
             )
        """
    ).fetchall()
    conn.close()

    if not low:
        return None

    # Generate PO number
    import time as _t
    po_number = f"AUTO-{_t.strftime('%Y%m%d-%H%M%S')}"

    conn = get_conn(tid)
    cur = conn.execute(
        "INSERT INTO purchase_orders (po_number, status, notes) VALUES (?,?,?)",
        (po_number, "DRAFT", "Auto-generated: stock fell below minimum level")
    )
    po_id = cur.lastrowid
    for p in low:
        needed = max(1, (p["reorder_level"] or 0) * 2 - p["current_stock"])
        conn.execute(
            """INSERT INTO purchase_order_items
               (po_id, product_code, product_name, unit, current_stock, reorder_level,
                order_quantity, previous_cost_price, estimated_total)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (po_id, p["code"], p["name"], p["unit"] or "PCS",
             p["current_stock"], p["reorder_level"], needed,
             p["last_cost_price"] or 0,
             needed * (p["last_cost_price"] or 0))
        )
    conn.commit()
    conn.close()
    return po_id, po_number, len(low)


# ── Payment History & Collections ────────────────────────────────────────────

def add_payment(tid, debtor_id, amount_paid, payment_date=None, payment_method="cash", notes="", recorded_by=""):
    from .validation import payment_amount
    amount_paid = payment_amount(amount_paid)
    payment_date = payment_date or date.today().isoformat()
    date.fromisoformat(payment_date)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM debtors WHERE id=?", (debtor_id,)).fetchone()
        if not row:
            raise ValueError("Debtor not found")
        debtor = dict(row)
        if amount_paid > outstanding_balance(debtor):
            raise ValueError("Payment exceeds the outstanding balance")
        total_paid = round(float(debtor.get("total_paid_to_date") or 0) + amount_paid, 2)
        remaining = round(float(debtor["amount_owed"]) - total_paid, 2)
        conn.execute("INSERT INTO payment_history (debtor_id,amount_paid,payment_date,payment_method,notes,recorded_by) VALUES (?,?,?,?,?,?)",
                     (debtor_id,amount_paid,payment_date,payment_method,notes,recorded_by))
        conn.execute("UPDATE debtors SET total_paid_to_date=?,last_payment_date=?,status=?,is_paid=? WHERE id=?",
                     (total_paid,payment_date,"PAID" if remaining <= 0 else "PARTIALLY_PAID",int(remaining <= 0),debtor_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_payment_history(tid, debtor_id):
    """Get all payments made by a debtor, newest first."""
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT * FROM payment_history WHERE debtor_id=?
           ORDER BY payment_date DESC, created_at DESC""",
        (debtor_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_debtor_payment_summary(tid, debtor_id):
    """Get total paid, remaining balance, and payment statistics for a debtor."""
    conn = get_conn(tid)
    debtor = conn.execute("SELECT * FROM debtors WHERE id=?", (debtor_id,)).fetchone()
    conn.close()
    if not debtor:
        return None

    debtor = dict(debtor)
    total_paid = debtor.get("total_paid_to_date", 0)
    amount_owed = debtor.get("amount_owed", 0)
    remaining = max(0, amount_owed - total_paid)

    payments = get_payment_history(tid, debtor_id)
    payment_count = len(payments)
    latest_payment = payments[0] if payments else None

    return {
        "debtor_id": debtor_id,
        "debtor_name": debtor.get("name", ""),
        "amount_owed": amount_owed,
        "total_paid": total_paid,
        "remaining": remaining,
        "status": debtor.get("status", "DUE"),
        "payment_count": payment_count,
        "latest_payment_date": latest_payment["payment_date"] if latest_payment else None,
        "latest_payment_amount": latest_payment["amount_paid"] if latest_payment else None,
    }


def delete_payment(tid, payment_id):
    """Delete a payment and recalculate debtor balance."""
    conn = get_conn(tid)
    payment = conn.execute("SELECT debtor_id FROM payment_history WHERE id=?", (payment_id,)).fetchone()
    if not payment:
        conn.close()
        return False

    debtor_id = payment["debtor_id"]
    conn.execute("DELETE FROM payment_history WHERE id=?", (payment_id,))
    conn.commit()

    # Recalculate balance
    total_paid = conn.execute(
        "SELECT COALESCE(SUM(amount_paid),0) FROM payment_history WHERE debtor_id=?",
        (debtor_id,)
    ).fetchone()[0]

    debtor = conn.execute("SELECT * FROM debtors WHERE id=?", (debtor_id,)).fetchone()
    if debtor:
        remaining = debtor["amount_owed"] - total_paid
        if remaining <= 0:
            new_status = "PAID"
        elif total_paid > 0:
            new_status = "PARTIALLY_PAID"
        else:
            new_status = "DUE"

        last_payment = conn.execute(
            "SELECT payment_date FROM payment_history WHERE debtor_id=? ORDER BY payment_date DESC LIMIT 1",
            (debtor_id,)
        ).fetchone()

        conn.execute(
            """UPDATE debtors SET total_paid_to_date=?, last_payment_date=?, status=?, is_paid=?
               WHERE id=?""",
            (total_paid, last_payment["payment_date"] if last_payment else None,
             new_status, 1 if new_status == "PAID" else 0, debtor_id)
        )
        conn.commit()

    conn.close()
    return True


# ── Reminder History & Management ────────────────────────────────────────────

def add_reminder_sent(tid, debtor_id, method="email", status="sent", message_preview="", reminder_date=None):
    """Log that a reminder was sent to a debtor."""
    if reminder_date is None:
        reminder_date = date.today().isoformat()
    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT INTO reminder_history (debtor_id, reminder_date, method, status, message_preview)
               VALUES (?,?,?,?,?)""",
            (debtor_id, reminder_date, method, status, message_preview[:500] if message_preview else "")
        )
        # Update last_reminded on debtor
        conn.execute(
            "UPDATE debtors SET last_reminded=? WHERE id=?",
            (reminder_date, debtor_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        raise


def get_reminder_history(tid, debtor_id, limit=50):
    """Get all reminders sent to a debtor, newest first."""
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT * FROM reminder_history WHERE debtor_id=?
           ORDER BY reminder_date DESC, created_at DESC LIMIT ?""",
        (debtor_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pause_reminders(tid, debtor_id, pause_until_date):
    """Pause reminders for a debtor until a specific date."""
    conn = get_conn(tid)
    conn.execute(
        "UPDATE debtors SET reminders_paused_until=? WHERE id=?",
        (pause_until_date, debtor_id)
    )
    conn.commit()
    conn.close()


def resume_reminders(tid, debtor_id):
    """Resume reminders for a debtor (clear pause date)."""
    conn = get_conn(tid)
    conn.execute(
        "UPDATE debtors SET reminders_paused_until=NULL WHERE id=?",
        (debtor_id,)
    )
    conn.commit()
    conn.close()


def get_debtors_needing_reminders(tid):
    """Get all debtors whose next reminder is due (not paused, status not PAID)."""
    from datetime import datetime as _dt
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT * FROM debtors
           WHERE is_paid=0 AND (reminders_paused_until IS NULL OR reminders_paused_until < date('now'))
           ORDER BY last_reminded ASC, date_of_purchase ASC"""
    ).fetchall()
    conn.close()

    # Filter by next_reminder calculation
    result = []
    for row in rows:
        debtor = dict(row)
        nxt, status = next_reminder(debtor)
        if status in ("overdue", "due_today"):
            result.append(debtor)
    return result


def count_overdue_debtors(tid):
    """Count debtors with overdue reminders."""
    debtors = get_all_debtors(tid, show_paid=False)
    count = 0
    for d in debtors:
        _, status = next_reminder(d)
        if status == "overdue":
            count += 1
    return count


def count_partially_paid_debtors(tid):
    """Count debtors with partial payments."""
    conn = get_conn(tid)
    count = conn.execute(
        "SELECT COUNT(*) FROM debtors WHERE status='PARTIALLY_PAID'"
    ).fetchone()[0]
    conn.close()
    return count


# ── Import History ────────────────────────────────────────────────────────────

def log_debtor_import(tid, source, added, updated, skipped, error_msg=""):
    """Log a debtor import event."""
    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT INTO notification_log (notification_type, recipient, message, status)
               VALUES (?,?,?,?)""",
            ("debtor_import", f"added={added}, updated={updated}, skipped={skipped}",
             f"Import from {source}: {added} added, {updated} updated, {skipped} skipped" + (f". Error: {error_msg}" if error_msg else ""),
             "success" if not error_msg else "error")
        )
        conn.commit()
        conn.close()
    except Exception:
        conn.close()
        pass


def log_product_import(tid, source, added, updated, skipped, error_msg=""):
    """Log a product import event."""
    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT INTO notification_log (notification_type, recipient, message, status)
               VALUES (?,?,?,?)""",
            ("product_import", f"added={added}, updated={updated}, skipped={skipped}",
             f"Import from {source}: {added} added, {updated} updated, {skipped} skipped" + (f". Error: {error_msg}" if error_msg else ""),
             "success" if not error_msg else "error")
        )
        conn.commit()
        conn.close()
    except Exception:
        conn.close()
        pass


def get_debtor_import_history(tid, limit=20):
    """Get recent debtor import events."""
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT created_at, recipient, message, status FROM notification_log
           WHERE notification_type='debtor_import'
           ORDER BY created_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_import_history(tid, limit=20):
    """Get recent product import events."""
    conn = get_conn(tid)
    rows = conn.execute(
        """SELECT created_at, recipient, message, status FROM notification_log
           WHERE notification_type='product_import'
           ORDER BY created_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Synchronization and Audit Logging ──────────────────────────────────────────

def log_sync_audit(tid, debtor_id, external_key, field_name, old_value, new_value, sync_source="api", sync_action="update"):
    """Log a synchronization change to audit trail."""
    try:
        conn = get_conn(tid)
        conn.execute("""
            INSERT INTO sync_audit_log (debtor_id, external_key, field_name, old_value, new_value, sync_source, sync_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (debtor_id, external_key, field_name, str(old_value)[:500], str(new_value)[:500], sync_source, sync_action))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging sync audit: {e}")


def get_sync_audit_log(tid, limit=100):
    """Get sync audit history."""
    try:
        conn = get_conn(tid)
        rows = conn.execute("""
            SELECT id, debtor_id, external_key, field_name, old_value, new_value, sync_source, sync_action, created_at
            FROM sync_audit_log
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []


def get_sync_status(tid):
    """Get overall sync status for this tenant."""
    try:
        conn = get_conn(tid)
        last_sync = conn.execute("""
            SELECT MAX(last_synced_at) as last_sync FROM debtors WHERE external_key IS NOT NULL
        """).fetchone()
        conn.close()
        if last_sync and last_sync["last_sync"]:
            return {"last_synced_at": last_sync["last_sync"], "status": "synced"}
        return {"last_synced_at": None, "status": "never"}
    except:
        return {"last_synced_at": None, "status": "error"}


def update_debtor_from_sync(tid, debtor_id, external_key, updates, sync_source="api"):
    """Update a debtor from sync data and log changes."""
    try:
        debtor = get_debtor(tid, debtor_id)
        if not debtor:
            return False

        logged_changes = {}
        for field, new_value in updates.items():
            old_value = debtor.get(field, "")
            if str(old_value) != str(new_value):
                logged_changes[field] = (old_value, new_value)
                log_sync_audit(tid, debtor_id, external_key, field, old_value, new_value, sync_source, "update")

        if logged_changes:
            update_debtor(tid, debtor_id, last_synced_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **updates)
            return True
        return False
    except Exception as e:
        print(f"Error updating debtor from sync: {e}")
        return False


def mark_debtor_removed_from_source(tid, debtor_id, external_key, sync_source="api"):
    """Mark a debtor as removed from external source (safe deletion)."""
    try:
        conn = get_conn(tid)
        conn.execute("""
            UPDATE debtors
            SET sync_status = 'removed_from_source', last_synced_at = ?
            WHERE id = ?
        """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), debtor_id))
        conn.commit()
        conn.close()
        log_sync_audit(tid, debtor_id, external_key, "sync_status", "synced", "removed_from_source", sync_source, "delete_mark")
        return True
    except Exception as e:
        print(f"Error marking debtor as removed: {e}")
        return False
