"""Services sold in POS and on job cards. Services are not stock, so selling one never changes stock.

A service is deactivated, never deleted: existing invoices and job cards keep referring to it. Each sale and
job line also stores the price it was sold at, so changing a service's price does not alter history.
"""
from ..tenant_db import get_conn
from .job_cards import JobCardError

NAME_MAX = 100
DESCRIPTION_MAX = 500
CATEGORY_MAX = 60
PRICE_MAX = 10_000_000


def _clean(name, description, category, price):
    name = str(name or "").strip()
    description = str(description or "").strip()
    category = str(category or "").strip()
    if not name:
        raise JobCardError("Enter the service name")
    if len(name) > NAME_MAX:
        raise JobCardError(f"Service name is too long (maximum {NAME_MAX} characters)")
    if len(description) > DESCRIPTION_MAX:
        raise JobCardError(f"Description is too long (maximum {DESCRIPTION_MAX} characters)")
    if len(category) > CATEGORY_MAX:
        raise JobCardError(f"Category is too long (maximum {CATEGORY_MAX} characters)")
    try:
        amount = round(float(price), 2)
    except (TypeError, ValueError):
        raise JobCardError("Enter the selling price as a number")
    if not (0 <= amount <= PRICE_MAX):
        raise JobCardError(f"The selling price must be between 0 and {PRICE_MAX:,}")
    return name, description, category, amount


def _name_taken(conn, name, exclude_id=None):
    row = conn.execute("SELECT id FROM services WHERE lower(trim(name)) = lower(?) AND id IS NOT ?",
                       (name, exclude_id)).fetchone()
    return row is not None


def get_service(tid, service_id):
    conn = get_conn(tid)
    try:
        row = conn.execute("SELECT * FROM services WHERE id=?", (int(service_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_services(tid, query="", include_inactive=True):
    conn = get_conn(tid)
    try:
        sql, params = "SELECT * FROM services WHERE 1=1", []
        if not include_inactive:
            sql += " AND is_active=1"
        term = str(query or "").strip()
        if term:
            sql += " AND (name LIKE ? OR category LIKE ? OR description LIKE ?)"
            params += [f"%{term}%", f"%{term}%", f"%{term}%"]
        sql += " ORDER BY is_active DESC, name"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def create_service(tid, name, default_price, description="", category=""):
    name, description, category, amount = _clean(name, description, category, default_price)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _name_taken(conn, name):
            raise JobCardError("A service with this name already exists")
        cur = conn.execute("""INSERT INTO services (name, description, default_price, category, is_active)
                              VALUES (?,?,?,?,1)""", (name, description, amount, category))
        conn.commit()
        service_id = cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_service(tid, service_id)


def update_service(tid, service_id, name, default_price, description="", category="", is_active=True):
    name, description, category, amount = _clean(name, description, category, default_price)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM services WHERE id=?", (int(service_id),)).fetchone():
            raise JobCardError("Service not found")
        if _name_taken(conn, name, exclude_id=int(service_id)):
            raise JobCardError("A service with this name already exists")
        conn.execute("""UPDATE services SET name=?, description=?, default_price=?, category=?, is_active=?,
                        updated_at=datetime('now') WHERE id=?""",
                     (name, description, amount, category, 1 if is_active else 0, int(service_id)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_service(tid, service_id)
