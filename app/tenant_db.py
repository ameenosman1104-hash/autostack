"""Per-tenant SQLite database — same schema as the desktop app."""
import sqlite3, os
from datetime import datetime, date, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tenants")


def _db_path(tenant_id):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        test_path = os.path.join(DATA_DIR, f"{tenant_id}.db")
        sqlite3.connect(test_path).close()
        return test_path
    except:
        return f":memory:?cache=shared&uri=file:tenant_{tenant_id}"


def get_conn(tenant_id):
    try:
        db_path = _db_path(tenant_id)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    except:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    # migrate missing columns added after initial release
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
        if "min_level_manual" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN min_level_manual INTEGER DEFAULT 0")
            conn.commit()
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))")
            conn.commit()
        if "extra_data" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN extra_data TEXT DEFAULT '{}'")
            conn.commit()
        if "deleted" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN deleted INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE products ADD COLUMN deleted_at TEXT DEFAULT NULL")
            conn.commit()
    except Exception as e:
        print(f"Migration error (may be expected for new DBs): {e}")
        pass
    # migrate debtor columns for collections tracking
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(debtors)").fetchall()]
        if "status" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN status TEXT DEFAULT 'DUE'")
            conn.commit()
        if "reminders_paused_until" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN reminders_paused_until TEXT DEFAULT NULL")
            conn.commit()
        if "last_payment_date" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN last_payment_date TEXT DEFAULT NULL")
            conn.commit()
        if "total_paid_to_date" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN total_paid_to_date REAL DEFAULT 0")
            conn.commit()
        if "next_reminder_date" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN next_reminder_date TEXT DEFAULT NULL")
            conn.commit()
        if "reminder_mode" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN reminder_mode TEXT DEFAULT 'default'")
            conn.commit()
        if "reminder_interval_days" not in cols:
            conn.execute("ALTER TABLE debtors ADD COLUMN reminder_interval_days INTEGER DEFAULT 28")
            conn.commit()
    except Exception as e:
        print(f"Debtor migration error (may be expected for new DBs): {e}")
        pass
    # ensure new tables exist (for databases created before these were added)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER,
            product_code TEXT DEFAULT '', product_name TEXT DEFAULT '',
            qty_sold REAL DEFAULT 0, sale_price REAL DEFAULT 0,
            total_amount REAL DEFAULT 0, notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stock_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER,
            product_code TEXT DEFAULT '', product_name TEXT DEFAULT '',
            change_type TEXT DEFAULT '', qty_before REAL DEFAULT 0,
            qty_after REAL DEFAULT 0, change_by REAL DEFAULT 0,
            notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            phone TEXT DEFAULT '', email TEXT DEFAULT '', address TEXT DEFAULT '',
            lead_time_days INTEGER DEFAULT 0, payment_terms TEXT DEFAULT '',
            notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
        );
        """)
        conn.commit()
    except:
        pass
    return conn


def init_tenant_db(tenant_id):
    try:
        conn = get_conn(tenant_id)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            code                 TEXT UNIQUE NOT NULL,
            name                 TEXT NOT NULL,
            category             TEXT DEFAULT '',
            unit                 TEXT DEFAULT 'PCS',
            current_stock        REAL DEFAULT 0,
            reorder_level        REAL DEFAULT 0,
            min_level_manual     INTEGER DEFAULT 0,
            max_stock            REAL DEFAULT 0,
            last_cost_price      REAL DEFAULT 0,
            previous_cost_price  REAL DEFAULT 0,
            supplier             TEXT DEFAULT '',
            created_at           TEXT DEFAULT (datetime('now')),
            updated_at           TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS notification_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT,
            recipient         TEXT,
            message           TEXT,
            status            TEXT,
            created_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS debtors (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT NOT NULL,
            phone            TEXT DEFAULT '',
            email            TEXT DEFAULT '',
            amount_owed      REAL DEFAULT 0,
            date_of_purchase TEXT NOT NULL,
            notify_method    TEXT DEFAULT 'email',
            reminder_days    INTEGER DEFAULT 14,
            notes            TEXT DEFAULT '',
            products_owed    TEXT DEFAULT '',
            is_paid          INTEGER DEFAULT 0,
            last_reminded    TEXT DEFAULT '',
            created_at       TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            po_number   TEXT UNIQUE NOT NULL,
            order_date  TEXT DEFAULT (datetime('now')),
            status      TEXT DEFAULT 'DRAFT',
            notes       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id               INTEGER NOT NULL,
            product_code        TEXT,
            product_name        TEXT,
            unit                TEXT DEFAULT 'PCS',
            current_stock       REAL DEFAULT 0,
            reorder_level       REAL DEFAULT 0,
            order_quantity      REAL DEFAULT 0,
            previous_cost_price REAL DEFAULT 0,
            estimated_total     REAL DEFAULT 0,
            FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sales (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id   INTEGER,
            product_code TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            qty_sold     REAL DEFAULT 0,
            sale_price   REAL DEFAULT 0,
            total_amount REAL DEFAULT 0,
            notes        TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stock_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id   INTEGER,
            product_code TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            change_type  TEXT DEFAULT '',
            qty_before   REAL DEFAULT 0,
            qty_after    REAL DEFAULT 0,
            change_by    REAL DEFAULT 0,
            notes        TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS suppliers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            phone          TEXT DEFAULT '',
            email          TEXT DEFAULT '',
            address        TEXT DEFAULT '',
            lead_time_days INTEGER DEFAULT 0,
            payment_terms  TEXT DEFAULT '',
            notes          TEXT DEFAULT '',
            created_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS payment_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_id       INTEGER NOT NULL,
            amount_paid     REAL DEFAULT 0,
            payment_date    TEXT NOT NULL,
            payment_method  TEXT DEFAULT 'cash',
            notes           TEXT DEFAULT '',
            recorded_by     TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS reminder_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_id       INTEGER NOT NULL,
            reminder_date   TEXT NOT NULL,
            method          TEXT DEFAULT 'email',
            status          TEXT DEFAULT 'sent',
            message_preview TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
        );
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"init_tenant_db error: {e}")
        pass


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
        owed    = conn.execute("SELECT COALESCE(SUM(amount_owed),0) FROM debtors WHERE is_paid=0").fetchone()[0]
        conn.close()
        return {"total": total, "low_stock": low, "out_of_stock": out,
                "active_debtors": debtors, "total_owed": owed}
    except:
        return {"total": 0, "low_stock": 0, "out_of_stock": 0, "active_debtors": 0, "total_owed": 0}


# ── Debtors ───────────────────────────────────────────────────────────────────

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
    """Calculate and save next reminder date for a debtor using DEFAULT mode.

    CRITICAL:
    - Only recalculates if reminder_mode = "default"
    - If reminder_mode = "manual" or "custom_interval", leaves next_reminder_date unchanged
    - Reminder is calculated from purchase_date + default_interval_days

    Args:
        tid: Tenant ID
        did: Debtor ID

    Returns:
        Next reminder date as string (YYYY-MM-DD format) or None if error
    """
    try:
        conn = get_conn(tid)
        debtor = conn.execute("SELECT * FROM debtors WHERE id=?", (did,)).fetchone()
        conn.close()

        if not debtor:
            return None

        # Only recalculate if using default mode
        mode = debtor.get("reminder_mode", "default")
        if mode in ("manual", "custom_interval"):
            # Custom mode set - do NOT recalculate, return existing date
            return debtor.get("next_reminder_date")

        # Calculate for DEFAULT mode: purchase_date + default_interval
        base_str = debtor["date_of_purchase"]
        base = datetime.strptime(base_str, "%Y-%m-%d").date()

        # Get default days from settings
        default_days = get_setting(tid, "default_reminder_days", "28")
        days = int(default_days or 28)

        # Calculate next reminder date from purchase date + interval
        next_date = base + timedelta(days=days)
        next_date_str = next_date.strftime("%Y-%m-%d")

        # Update debtor with calculated date
        update_debtor(tid, did, next_reminder_date=next_date_str)

        return next_date_str
    except Exception as e:
        print(f"Error calculating next reminder: {e}")
        return None


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
    try:
        base_str = debtor["last_reminded"] if debtor.get("last_reminded") else debtor["date_of_purchase"]
        base = datetime.strptime(base_str, "%Y-%m-%d").date()
        nxt  = base + timedelta(days=int(debtor.get("reminder_days", 14)))
        today = date.today()
        if nxt < today:
            return nxt.strftime("%d %b %Y"), "overdue"
        elif nxt == today:
            return "Today", "due_today"
        else:
            return nxt.strftime("%d %b %Y"), "ok"
    except Exception:
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
        "SELECT amount_owed, date_of_purchase FROM debtors WHERE is_paid=0"
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
    """Record a payment against a debtor. Automatically updates debtor status and balance."""
    if payment_date is None:
        payment_date = date.today().isoformat()
    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT INTO payment_history (debtor_id, amount_paid, payment_date, payment_method, notes, recorded_by)
               VALUES (?,?,?,?,?,?)""",
            (debtor_id, amount_paid, payment_date, payment_method, notes, recorded_by)
        )
        conn.commit()

        # Update debtor: recalculate total_paid_to_date and update last_payment_date
        total_paid = conn.execute(
            "SELECT COALESCE(SUM(amount_paid),0) FROM payment_history WHERE debtor_id=?",
            (debtor_id,)
        ).fetchone()[0]

        debtor = get_debtor(tid, debtor_id)
        if debtor:
            remaining = debtor["amount_owed"] - total_paid
            # Determine new status
            if remaining <= 0:
                new_status = "PAID"
            elif total_paid > 0:
                new_status = "PARTIALLY_PAID"
            else:
                new_status = "DUE"

            conn.execute(
                """UPDATE debtors SET total_paid_to_date=?, last_payment_date=?, status=?, is_paid=?
                   WHERE id=?""",
                (total_paid, payment_date, new_status, 1 if new_status == "PAID" else 0, debtor_id)
            )
            conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        raise


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
