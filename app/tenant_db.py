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
    # ensure new tables exist (for databases created before these were added)
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
