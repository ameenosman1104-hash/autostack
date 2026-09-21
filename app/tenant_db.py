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
    from app.credential_store import decrypt_value
    conn = get_conn(tid)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    value = row["value"] if row else default
    return decrypt_value(value) if value else value


def save_setting(tid, key, value):
    from app.credential_store import encrypt_value, should_encrypt_key
    conn = get_conn(tid)
    # Encrypt if it's a sensitive key and has a value
    if should_encrypt_key(key) and value:
        value = encrypt_value(str(value))
    else:
        value = str(value)
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def delete_setting(tid, key):
    """Delete a setting from the database."""
    conn = get_conn(tid)
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()


def get_gmail_token_raw(tid):
    """Get raw (encrypted) Gmail OAuth token from database."""
    conn = get_conn(tid)
    try:
        row = conn.execute(
            "SELECT id, authorized_email, access_token, refresh_token, expires_at FROM gmail_oauth_tokens LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except:
        conn.close()
        return None


def has_gmail_token(tid):
    """Check if a tenant has a Gmail OAuth token."""
    conn = get_conn(tid)
    try:
        row = conn.execute("SELECT 1 FROM gmail_oauth_tokens LIMIT 1").fetchone()
        conn.close()
        return bool(row)
    except:
        conn.close()
        return False


def get_all_settings(tid):
    from app.credential_store import decrypt_value
    conn = get_conn(tid)
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: decrypt_value(r["value"]) if r["value"] else r["value"] for r in rows}


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


def add_debtor(tid, name=None, phone=None, email=None, amount_owed=None,
               date_of_purchase=None, notify_method=None, reminder_days=None,
               notes=None, products_owed="", **kwargs):
    """Add a new debtor. Accepts both positional and keyword arguments.

    Keyword arguments can also include sync metadata: external_key, external_source,
    reminder_mode, reminder_interval_days, next_reminder_date, etc.
    """
    conn = get_conn(tid)

    # Build insert with all provided columns
    columns = ["name", "phone", "email", "amount_owed", "date_of_purchase",
               "notify_method", "reminder_days", "notes", "products_owed"]
    values = [name, phone, email, amount_owed, date_of_purchase,
              notify_method, reminder_days, notes, products_owed]

    # Add any extra kwargs that correspond to database columns
    extra_cols = {"external_key", "external_source", "reminder_mode",
                  "reminder_interval_days", "next_reminder_date", "last_payment_date",
                  "total_paid_to_date", "is_paid", "status", "reminders_paused_until",
                  "last_reminded", "sync_status", "due_date", "sync_snapshot", "sync_pending"}

    for key in sorted(extra_cols):
        if key in kwargs:
            columns.append(key)
            values.append(kwargs[key])

    # Create parameterized insert
    placeholders = ",".join("?" * len(columns))
    column_list = ",".join(columns)

    try:
        cur = conn.execute(
            f"INSERT INTO debtors ({column_list}) VALUES ({placeholders})",
            values
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_debtor(tid, did, **kwargs):
    conn = get_conn(tid)
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [did]
    conn.execute(f"UPDATE debtors SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def reminder_date(debtor, default_days):
    """Resolve the saved schedule, never treating a cached date as an interval."""
    from .validation import reminder_interval
    if debtor.get("reminder_mode") in ("manual", "custom"):
        return debtor.get("next_reminder_date")
    if not debtor.get("date_of_purchase"):
        raise ValueError("Debtor missing purchase date")
    days = reminder_interval(debtor.get("reminder_interval_days") if debtor.get("reminder_mode") == "custom_interval" else default_days)
    base = date.fromisoformat(debtor["date_of_purchase"])
    next_date = base + timedelta(days=days)
    if debtor.get("last_reminded"):
        last = date.fromisoformat(debtor["last_reminded"][:10])
        if last >= next_date:
            next_date = base + timedelta(days=((last-base).days // days + 1) * days)
    return next_date.isoformat()


def calculate_next_reminder(tid, did):
    """Persist the same date used by the table and reminder scheduler."""
    debtor = get_debtor(tid, did)
    if not debtor:
        return None
    value = reminder_date(debtor, get_setting(tid, "default_reminder_days", "28"))
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
        schedule = dict(debtor, reminder_mode="custom_interval", reminder_interval_days=interval_days)
        next_date_str = reminder_date(schedule, 28)

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


def next_reminder(debtor, tid=None):
    """Display the resolved schedule; exact manual dates remain authoritative."""
    try:
        default_days = get_setting(tid, "default_reminder_days", "28") if tid is not None else 28
        value = reminder_date(debtor, default_days)
        if not value:
            return "—", "unscheduled"
        nxt = date.fromisoformat(value)

        today = date.today()
        if nxt < today:
            return nxt.strftime("%d %b %Y"), "overdue"
        elif nxt == today:
            return "Today", "due_today"
        else:
            return nxt.strftime("%d %b %Y"), "ok"
    except Exception as e:
        print(f"Error calculating next_reminder: {e}")
        return "—", "unscheduled"


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
        nxt, status = next_reminder(debtor, tid)
        if status in ("overdue", "due_today"):
            result.append(debtor)
    return result


def count_overdue_debtors(tid):
    """Count debtors with overdue reminders."""
    debtors = get_all_debtors(tid, show_paid=False)
    count = 0
    for d in debtors:
        _, status = next_reminder(d, tid)
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


# ── POS: Customers ────────────────────────────────────────────────────────────

def add_customer(tid, name, phone="", email="", vehicle_registration="", notes="", customer_type="regular"):
    """Add a new customer."""
    conn = get_conn(tid)
    try:
        cur = conn.execute(
            """INSERT INTO customers (name, phone, email, vehicle_registration, notes, customer_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, phone, email, vehicle_registration, notes, customer_type)
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_customer(tid, customer_id):
    """Get customer by ID."""
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_customer_by_name(tid, name):
    """Search for customer by name (case-insensitive)."""
    conn = get_conn(tid)
    row = conn.execute(
        "SELECT * FROM customers WHERE LOWER(name) LIKE LOWER(?) ORDER BY name LIMIT 1",
        (f"%{name}%",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_customers(tid):
    """Get all customers."""
    conn = get_conn(tid)
    rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_customers(tid, query):
    """Search customers by name, phone, or vehicle registration.

    Args:
        tid: Tenant ID
        query: Search string (name, phone, or vehicle registration)

    Returns:
        List of matching customer dictionaries
    """
    conn = get_conn(tid)
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? OR vehicle_registration LIKE ? ORDER BY name",
        (q, q, q)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_customer(tid, customer_id, **kwargs):
    """Update customer fields."""
    conn = get_conn(tid)
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [customer_id]
    conn.execute(f"UPDATE customers SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def get_debtor_by_customer_id(tid, customer_id):
    """Get debtor linked to a customer (returns first active debtor)."""
    conn = get_conn(tid)
    row = conn.execute(
        "SELECT * FROM debtors WHERE customer_id=? AND is_paid=0",
        (customer_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── POS: Invoices (Transactions) ──────────────────────────────────────────────

def add_invoice(tid, invoice_number, idempotency_key, customer_id, sale_date, total=0,
                payment_method='cash', created_by=None, debtor_id=None, status='completed',
                subtotal=0, discount=0, tax=0):
    """Create a new invoice (transaction record)."""
    conn = get_conn(tid)
    try:
        cur = conn.execute(
            """INSERT INTO invoices
               (invoice_number, idempotency_key, customer_id, sale_date, total,
                payment_method, created_by, debtor_id, status, subtotal, discount, tax)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (invoice_number, idempotency_key, customer_id, sale_date, total,
             payment_method, created_by, debtor_id, status, subtotal, discount, tax)
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "invoice_number" in str(e):
            raise ValueError("Invoice number already exists")
        elif "idempotency_key" in str(e):
            raise ValueError("This request was already processed")
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_invoice(tid, invoice_id):
    """Get invoice by ID."""
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_invoice_by_idempotency_key(tid, idempotency_key):
    """Get invoice by idempotency key (for preventing duplicates)."""
    conn = get_conn(tid)
    row = conn.execute(
        "SELECT * FROM invoices WHERE idempotency_key=?",
        (idempotency_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_invoices(tid, limit=200, offset=0):
    """Get invoices for tenant, newest first."""
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM invoices ORDER BY sale_date DESC, created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_invoices_by_customer(tid, customer_id):
    """Get all invoices for a customer."""
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM invoices WHERE customer_id=? ORDER BY sale_date DESC",
        (customer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_invoice(tid, invoice_id, **kwargs):
    """Update invoice fields."""
    conn = get_conn(tid)
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [invoice_id]
    conn.execute(f"UPDATE invoices SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


# ── POS: Invoice Items (Line Items) ──────────────────────────────────────────

def add_invoice_item(tid, invoice_id, item_type, quantity, unit_price,
                     product_id=None, service_id=None, discount=0):
    """Add a line item to an invoice."""
    conn = get_conn(tid)
    total = quantity * unit_price
    try:
        cur = conn.execute(
            """INSERT INTO invoice_items
               (invoice_id, item_type, product_id, service_id, quantity, unit_price, total, discount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (invoice_id, item_type, product_id, service_id, quantity, unit_price, total, discount)
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_invoice_items(tid, invoice_id):
    """Get all line items for an invoice."""
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id",
        (invoice_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── POS: Services ────────────────────────────────────────────────────────────

def add_service(tid, name, default_price=0, description="", category=""):
    """Add a new service."""
    conn = get_conn(tid)
    try:
        cur = conn.execute(
            """INSERT INTO services (name, default_price, description, category)
               VALUES (?, ?, ?, ?)""",
            (name, default_price, description, category)
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_service(tid, service_id):
    """Get service by ID."""
    conn = get_conn(tid)
    row = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_services(tid, active_only=True):
    """Get all services."""
    conn = get_conn(tid)
    if active_only:
        rows = conn.execute("SELECT * FROM services WHERE is_active=1 ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_service(tid, service_id, **kwargs):
    """Update service fields."""
    conn = get_conn(tid)
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [service_id]
    conn.execute(f"UPDATE services SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


# ── PHASE C.0: Customer Aggregate Debt ────────────────────────────────────────

def get_debtors_by_customer_id(tid, customer_id):
    """Get all unpaid debtors for a customer (Phase C.0: multiple debtors per customer)."""
    conn = get_conn(tid)
    rows = conn.execute(
        "SELECT * FROM debtors WHERE customer_id=? AND is_paid=0 ORDER BY created_at",
        (customer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def customer_total_outstanding(tid, customer_id):
    """Calculate total outstanding balance across all unpaid debtors for a customer."""
    debtors = get_debtors_by_customer_id(tid, customer_id)
    total = 0.0
    for debtor in debtors:
        total += outstanding_balance(debtor)
    return round(total, 2)


def _allocate_invoice_number(conn, tid):
    """Allocate next invoice number within an existing transaction (no commit)."""
    row = conn.execute(
        "SELECT next_number FROM invoice_sequences WHERE tid = ?",
        (tid,)
    ).fetchone()

    if not row:
        conn.execute(
            "INSERT INTO invoice_sequences (tid, next_number) VALUES (?, ?)",
            (tid, 1)
        )
        next_num = 1
    else:
        next_num = row[0]

    # Increment for next time
    conn.execute(
        "UPDATE invoice_sequences SET next_number = ? WHERE tid = ?",
        (next_num + 1, tid)
    )

    return f"INV-{next_num:06d}"


# ── PHASE C.1: Atomic Sale Transaction Engine ─────────────────────────────────

def complete_sale(tid, idempotency_key, items,
                  customer_id=None, payment_method="cash", sale_date=None,
                  discount=0, notes="", created_by=None, invoice_number=None):
    """
    Atomically process a complete POS transaction.

    Invoice number is allocated internally, after idempotency check. This ensures
    no sequence numbers are wasted on failures or retries.

    Args:
        tid: Tenant ID
        idempotency_key: UUID/deduplication key
        items: List of line items:
            [{'type': 'product'|'service', 'id': <id>, 'quantity': <qty>}, ...]
        customer_id: Customer ID (required for credit, optional for cash/card/eft)
        payment_method: 'cash'|'card'|'eft'|'credit' (default: 'cash')
        sale_date: ISO date string (defaults to today)
        discount: Invoice-level discount amount (0 to subtotal)
        notes: Transaction notes
        created_by: User ID who recorded sale
        invoice_number: (Optional, for backward compat) Ignored; auto-allocated

    Returns:
        {
            'success': True,
            'duplicate': False,  # True if idempotent retry
            'invoice_id': <id>,
            'invoice_number': <str>,
            'total': <float>,
            'debtor_id': <id or None>,
            'items_count': <int>,
            'payment_status': 'paid'|'unpaid'
        }

    Raises:
        ValueError: Validation error (stock, customer, price, etc.)
        Exception: Database error (transaction rolled back)
    """
    from decimal import Decimal, ROUND_HALF_UP
    import json

    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── 1. Check idempotency inside transaction ──
        existing = conn.execute(
            "SELECT id FROM invoices WHERE idempotency_key=?",
            (idempotency_key,)
        ).fetchone()

        if existing:
            invoice_id = existing[0]
            invoice = conn.execute(
                "SELECT * FROM invoices WHERE id=?", (invoice_id,)
            ).fetchone()
            invoice = dict(invoice) if invoice else None
            items_data = conn.execute(
                "SELECT * FROM invoice_items WHERE invoice_id=?", (invoice_id,)
            ).fetchall()
            conn.commit()
            conn.close()
            return {
                'success': True,
                'duplicate': True,
                'invoice_id': invoice_id,
                'invoice_number': invoice['invoice_number'] if invoice else None,
                'total': float(invoice['total']) if invoice else 0,
                'debtor_id': invoice['debtor_id'] if invoice else None,
                'items_count': len(items_data),
                'payment_status': invoice['payment_status'] if invoice else 'paid'
            }

        # ── 2. Validate inputs ──
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if payment_method not in ['cash', 'card', 'eft', 'credit']:
            raise ValueError(f"Invalid payment_method: {payment_method}")
        if not items or len(items) == 0:
            raise ValueError("items list cannot be empty")
        if payment_method == 'credit' and customer_id is None:
            raise ValueError("credit payment requires customer_id")
        if discount < 0:
            raise ValueError("discount cannot be negative")

        # Allocate invoice number within transaction (after idempotency check)
        invoice_number = _allocate_invoice_number(conn, tid)

        # ── 3. Validate customer (if provided) ──
        if customer_id is not None:
            customer = conn.execute(
                "SELECT * FROM customers WHERE id=?",
                (customer_id,)
            ).fetchone()
            if not customer:
                raise ValueError(f"Customer ID {customer_id} not found")
            customer = dict(customer)
        else:
            customer = None

        # ── 4. Validate sale_date ──
        if sale_date is None:
            sale_date = date.today().isoformat()
        else:
            try:
                date.fromisoformat(sale_date)
            except ValueError:
                raise ValueError(f"Invalid sale_date format: {sale_date}")

        # ── 5. Aggregate product quantities by product_id ──
        items_by_product = {}
        items_by_service = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each item must be a dict")
            item_type = item.get('type')
            item_id = item.get('id')
            qty = item.get('quantity')

            if item_type not in ['product', 'service']:
                raise ValueError(f"Invalid item type: {item_type}")
            if not item_id or item_id <= 0:
                raise ValueError("Item id must be > 0")
            if not qty or qty <= 0:
                raise ValueError("Item quantity must be > 0")

            if item_type == 'product':
                if item_id not in items_by_product:
                    items_by_product[item_id] = 0
                items_by_product[item_id] += qty
            else:  # service
                if item_id not in items_by_service:
                    items_by_service[item_id] = 0
                items_by_service[item_id] += qty

        # ── 6. Validate products exist and have sufficient stock ──
        products_map = {}
        for product_id, total_qty in items_by_product.items():
            product = conn.execute(
                "SELECT * FROM products WHERE id=? AND (deleted=0 OR deleted IS NULL)",
                (product_id,)
            ).fetchone()
            if not product:
                raise ValueError(f"Product ID {product_id} not found or deleted")
            product = dict(product)
            products_map[product_id] = product

            if product['current_stock'] < total_qty:
                raise ValueError(
                    f"Insufficient stock for product '{product['name']}': "
                    f"have {product['current_stock']}, need {total_qty}"
                )

        # ── 7. Validate services exist and fetch prices ──
        services_map = {}
        for service_id, total_qty in items_by_service.items():
            service = conn.execute(
                "SELECT * FROM services WHERE id=? AND is_active=1",
                (service_id,)
            ).fetchone()
            if not service:
                raise ValueError(f"Service ID {service_id} not found or inactive")
            service = dict(service)
            services_map[service_id] = service

            if service['default_price'] is None or service['default_price'] < 0:
                raise ValueError(
                    f"Service '{service['name']}' has invalid price: {service['default_price']}"
                )

        # ── 8. Load authoritative prices and calculate totals ──
        item_details = []  # Track each line item for later INSERT
        subtotal_decimal = Decimal('0.00')

        for item in items:
            item_type = item.get('type')
            item_id = item.get('id')
            qty = Decimal(str(item.get('quantity')))

            if item_type == 'product':
                product = products_map[item_id]

                # Load selling price from column (authoritative)
                selling_price = product.get('selling_price')

                if selling_price is None:
                    raise ValueError(
                        f"Product '{product['name']}' has no selling price configured. "
                        "Please set selling price in inventory before sale."
                    )

                try:
                    unit_price = Decimal(str(selling_price))
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Product '{product['name']}' has invalid selling_price: {selling_price}"
                    )

                if unit_price < 0:
                    raise ValueError(
                        f"Product '{product['name']}' selling_price cannot be negative: {unit_price}"
                    )

            else:  # service
                service = services_map[item_id]
                try:
                    unit_price = Decimal(str(service['default_price']))
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Service '{service['name']}' has invalid default_price: {service['default_price']}"
                    )

            item_total = (qty * unit_price).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            subtotal_decimal += item_total

            item_details.append({
                'type': item_type,
                'id': item_id,
                'quantity': float(qty),
                'unit_price': float(unit_price),
                'total': float(item_total)
            })

        # ── 9. Validate discount and calculate total ──
        discount_decimal = Decimal(str(discount)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        if discount_decimal < 0 or discount_decimal > subtotal_decimal:
            raise ValueError(
                f"discount must be between 0 and subtotal ({float(subtotal_decimal)})"
            )

        # Phase C.1: tax = 0 (no VAT engine yet)
        tax_decimal = Decimal('0.00')

        total_decimal = (subtotal_decimal - discount_decimal + tax_decimal).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        if total_decimal < 0:
            raise ValueError("Total cannot be negative")

        # ── 10. Determine payment status ──
        if payment_method in ['cash', 'card', 'eft']:
            payment_status = 'paid'
            new_debtor_id = None
        else:  # credit
            payment_status = 'unpaid'
            new_debtor_id = None  # Will be set after debtor creation

        # ── 11. Create invoice ──
        invoice_id = conn.execute(
            """INSERT INTO invoices
               (invoice_number, idempotency_key, customer_id, debtor_id,
                sale_date, status, subtotal, discount, tax, total,
                payment_method, payment_status, notes, created_by,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (invoice_number, idempotency_key, customer_id, None,
             sale_date, 'completed', float(subtotal_decimal), float(discount_decimal),
             float(tax_decimal), float(total_decimal),
             payment_method, payment_status, notes, created_by)
        ).lastrowid

        # ── 12. Create invoice items ──
        for item_detail in item_details:
            conn.execute(
                """INSERT INTO invoice_items
                   (invoice_id, item_type, product_id, service_id,
                    quantity, unit_price, total, discount)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (invoice_id,
                 item_detail['type'],
                 item_detail['id'] if item_detail['type'] == 'product' else None,
                 item_detail['id'] if item_detail['type'] == 'service' else None,
                 item_detail['quantity'],
                 item_detail['unit_price'],
                 item_detail['total'],
                 0)
            )

        # ── 13. Deduct product stock (defensive UPDATE) ──
        for product_id, product in products_map.items():
            total_qty_needed = items_by_product[product_id]
            qty_before = product['current_stock']

            cursor = conn.execute(
                """UPDATE products
                   SET current_stock = current_stock - ?,
                       updated_at = datetime('now')
                   WHERE id = ? AND current_stock >= ?""",
                (total_qty_needed, product_id, total_qty_needed)
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    f"Stock update failed for product ID {product_id} "
                    "(possible concurrent sale)"
                )

            # Fetch updated stock
            updated_product = conn.execute(
                "SELECT current_stock FROM products WHERE id=?",
                (product_id,)
            ).fetchone()
            qty_after = updated_product[0]

            # ── 14. Log to stock_history ──
            conn.execute(
                """INSERT INTO stock_history
                   (product_id, product_code, product_name,
                    change_type, qty_before, qty_after, change_by, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (product_id, product['code'], product['name'],
                 'sale', qty_before, qty_after, qty_after - qty_before,
                 f"Invoice {invoice_number}")
            )

        # ── 15. Handle credit sale (create debtor) ──
        if payment_method == 'credit':
            new_debtor_id = conn.execute(
                """INSERT INTO debtors
                   (name, customer_id, amount_owed, date_of_purchase,
                    status, is_paid, notify_method, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (customer['name'] if customer else 'Unknown',
                 customer_id,
                 float(total_decimal),
                 sale_date,
                 'DUE',
                 0,
                 'email')
            ).lastrowid

            # Link debtor to invoice
            conn.execute(
                "UPDATE invoices SET debtor_id=? WHERE id=?",
                (new_debtor_id, invoice_id)
            )

        # ── 16. COMMIT ──
        conn.commit()

        return {
            'success': True,
            'duplicate': False,
            'invoice_id': invoice_id,
            'invoice_number': invoice_number,
            'total': float(total_decimal),
            'debtor_id': new_debtor_id,
            'items_count': len(item_details),
            'payment_status': payment_status
        }

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
