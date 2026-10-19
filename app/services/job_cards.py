"""Job cards and vehicles: validation, numbering, status workflow and audit.

Phase 1 scope: no stock movement and no invoice conversion. Creating or editing a job
only warns about short stock. Stock is deducted later, only when a job is converted
through the POS transaction engine.
"""
import re
from datetime import date

from ..tenant_db import get_conn
from .quotations import MAX_QUANTITY, money, pricing_policy

STATUSES = ("open", "in_progress", "ready", "completed", "cancelled")
# 'ready' means work is finished and the car awaits collection. 'completed' means it was collected.
# Payment is tracked only on the linked invoice, never on the job card.
TRANSITIONS = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"ready", "cancelled"},
    "ready": {"completed", "in_progress", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
EDITABLE_STATUSES = {"open", "in_progress"}
REGISTRATION_RE = re.compile(r"^[A-Z0-9]{2,12}$")
MAX_MILEAGE = 2_000_000
MAX_TEXT = {"technician": 100, "work_performed": 4000, "notes": 2000, "make": 60, "model": 60, "vehicle_notes": 500}


class JobCardError(ValueError):
    """Validation or workflow error that is safe to show to the user."""


def normalise_registration(value):
    reg = re.sub(r"[\s\-]", "", str(value or "")).upper()
    if not REGISTRATION_RE.match(reg):
        raise JobCardError("Registration must be 2 to 12 letters or digits")
    return reg


def _whole_number(value, field, lo, hi):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise JobCardError(f"{field} must be a whole number")
    if not (lo <= number <= hi):
        raise JobCardError(f"{field} must be between {lo} and {hi}")
    return number


def _text(value, field):
    text = str(value or "").strip()
    limit = MAX_TEXT.get(field, 500)
    if len(text) > limit:
        raise JobCardError(f"{field.replace('_', ' ').capitalize()} is too long (maximum {limit} characters)")
    return text


def _iso_date(value, field):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise JobCardError(f"{field} must be a valid date (YYYY-MM-DD)")


def _event(conn, job_id, event, actor, detail=""):
    conn.execute("INSERT INTO job_card_events (job_card_id, event, actor, detail) VALUES (?,?,?,?)",
                 (job_id, event, actor or "", detail))


def _next_job_number(conn):
    value = int(conn.execute("SELECT next_number FROM job_card_sequence WHERE id=1").fetchone()[0])
    conn.execute("UPDATE job_card_sequence SET next_number=? WHERE id=1", (value + 1,))
    return f"JOB-{value:06d}"


# ── Vehicles ─────────────────────────────────────────────────────────────────

def upsert_vehicle(tid, customer_id, registration, make="", model="", year=None, notes="", actor=""):
    """Create a vehicle, or update its details if the registration already belongs to this customer.

    A registration that belongs to another customer is refused. Transfers need an admin workflow,
    which is not part of Phase 1.
    """
    reg = normalise_registration(registration)
    make, model, notes = _text(make, "make"), _text(model, "model"), _text(notes, "vehicle_notes")
    year = _whole_number(year, "Year", 1950, 2100)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM customers WHERE id=?", (int(customer_id),)).fetchone():
            raise JobCardError("Customer not found")
        row = conn.execute("SELECT id, customer_id FROM vehicles WHERE registration=?", (reg,)).fetchone()
        if row:
            if row["customer_id"] != int(customer_id):
                raise JobCardError("This registration belongs to another customer")
            conn.execute("""UPDATE vehicles SET make=COALESCE(NULLIF(?, ''), make),
                            model=COALESCE(NULLIF(?, ''), model), year=COALESCE(?, year),
                            notes=COALESCE(NULLIF(?, ''), notes), updated_at=datetime('now') WHERE id=?""",
                         (make, model, year, notes, row["id"]))
            vehicle_id = row["id"]
        else:
            cur = conn.execute("INSERT INTO vehicles (customer_id, registration, make, model, year, notes) VALUES (?,?,?,?,?,?)",
                               (int(customer_id), reg, make, model, year, notes))
            vehicle_id = cur.lastrowid
        conn.commit()
        return get_vehicle(tid, vehicle_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_vehicle(tid, vehicle_id):
    conn = get_conn(tid)
    try:
        row = conn.execute("SELECT * FROM vehicles WHERE id=?", (int(vehicle_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_vehicles(tid, customer_id=None, search=None, limit=50):
    conn = get_conn(tid)
    try:
        sql, params = "SELECT * FROM vehicles WHERE 1=1", []
        if customer_id is not None:
            sql += " AND customer_id=?"
            params.append(int(customer_id))
        if search:
            sql += " AND (registration LIKE ? OR make LIKE ? OR model LIKE ?)"
            params += [f"%{search.upper()}%", f"%{search}%", f"%{search}%"]
        sql += " ORDER BY registration LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def vehicle_history(tid, vehicle_id):
    """Every job card for a vehicle, newest first, with the linked invoice number when converted."""
    conn = get_conn(tid)
    try:
        rows = conn.execute("""SELECT j.id, j.job_number, j.status, j.mileage_in, j.mileage_out, j.opened_at,
                                      j.completed_at, j.total, i.invoice_number
                               FROM job_cards j LEFT JOIN invoices i ON i.id = j.invoice_id
                               WHERE j.vehicle_id=? ORDER BY j.id DESC""", (int(vehicle_id),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Items and stock warnings ─────────────────────────────────────────────────

def _load_items(conn, raw_items, policy):
    if not raw_items:
        raise JobCardError("Add at least one item")
    rows = []
    for index, raw in enumerate(raw_items, start=1):
        kind = raw.get("item_type")
        qty = _whole_or_fraction(raw.get("quantity"), f"Quantity on line {index}")
        discount = _whole_or_fraction(raw.get("discount_pct", 0) or 0, f"Discount on line {index}", 0, 100)
        if discount > policy["max_discount"]:
            raise JobCardError(f"Discount on line {index} exceeds your limit of {policy['max_discount']:g}%")
        if kind == "product":
            product = conn.execute(
                "SELECT id, name, code, brand, tyre_size, pattern, selling_price FROM products WHERE id=? AND deleted=0",
                (_int(raw.get("product_id"), index),)).fetchone()
            if not product:
                raise JobCardError(f"Product not found on line {index}")
            base = float(product["selling_price"] or 0)
            description = product["name"] or product["code"]
            detail = " ".join(p for p in (product["brand"], product["tyre_size"], product["pattern"]) if p)
            product_id, service_id = product["id"], None
        elif kind == "service":
            service = conn.execute("SELECT id, name, description, default_price FROM services WHERE id=? AND is_active=1",
                                   (_int(raw.get("service_id"), index),)).fetchone()
            if not service:
                raise JobCardError(f"Service not found on line {index}")
            base = float(service["default_price"] or 0)
            description = service["name"]
            detail = service["description"] or ""
            product_id, service_id = None, service["id"]
        else:
            raise JobCardError(f"Line {index} must be a product or a service")
        price = _whole_or_fraction(raw.get("unit_price", base), f"Unit price on line {index}", 0, 10_000_000)
        if abs(price - base) > 0.005 and not policy["is_admin"]:
            raise JobCardError(f"You are not authorised to change the price on line {index}")
        rows.append({
            "position": index, "item_type": kind, "product_id": product_id, "service_id": service_id,
            "description": str(description)[:200], "detail": str(detail)[:500], "quantity": qty,
            "base_price": money(base), "unit_price": money(price), "discount_pct": discount,
            "line_total": money(qty * price * (1 - discount / 100)),
        })
    return rows


def _int(value, index):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise JobCardError(f"Line {index} needs a valid product or service")


def _whole_or_fraction(value, field, lo=0.0001, hi=MAX_QUANTITY):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise JobCardError(f"{field} must be a number")
    if number != number or not (lo <= number <= hi):
        raise JobCardError(f"{field} must be between {lo:g} and {hi:g}")
    return number


def stock_shortages(conn, items):
    """Products whose requested quantity exceeds current stock. Warnings only; nothing is reserved."""
    need = {}
    for item in items:
        if item["item_type"] == "product":
            need[item["product_id"]] = need.get(item["product_id"], 0) + item["quantity"]
    shortages = []
    for product_id, qty in need.items():
        row = conn.execute("SELECT name, code, current_stock FROM products WHERE id=?", (product_id,)).fetchone()
        if row and float(row["current_stock"] or 0) < qty:
            shortages.append({"product_id": product_id, "description": row["name"] or row["code"],
                              "needed": qty, "available": float(row["current_stock"] or 0)})
    return shortages


def _insert_items(conn, job_id, items):
    for item in items:
        conn.execute("""INSERT INTO job_card_items (job_card_id, position, item_type, product_id, service_id,
                        description, detail, quantity, base_price, unit_price, discount_pct, line_total)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (job_id, item["position"], item["item_type"], item["product_id"], item["service_id"],
                      item["description"], item["detail"], item["quantity"], item["base_price"],
                      item["unit_price"], item["discount_pct"], item["line_total"]))


def _totals(items):
    subtotal = money(sum(i["quantity"] * i["unit_price"] for i in items))
    total = money(sum(i["line_total"] for i in items))
    return subtotal, money(subtotal - total), total


# ── Job cards ────────────────────────────────────────────────────────────────

def _header_fields(conn, data, customer_id, vehicle_id):
    """Validate the vehicle belongs to the customer, and copy the customer's contact details."""
    customer = conn.execute("SELECT name, phone, email FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        raise JobCardError("Customer not found")
    vehicle = conn.execute("SELECT customer_id FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not vehicle:
        raise JobCardError("Vehicle not found")
    if vehicle["customer_id"] != customer_id:
        raise JobCardError("That vehicle belongs to a different customer")
    mileage_in = _whole_number(data.get("mileage_in"), "Mileage in", 0, MAX_MILEAGE)
    promised = _iso_date(data.get("promised_date"), "Promised date")
    return {
        "customer_name": (customer["name"] or "")[:200],
        "customer_phone": (customer["phone"] or "")[:50],
        "customer_email": (customer["email"] or "")[:200],
        "mileage_in": mileage_in,
        "technician": _text(data.get("technician"), "technician"),
        "promised_date": promised,
        "work_performed": _text(data.get("work_performed"), "work_performed"),
        "notes": _text(data.get("notes"), "notes"),
    }


def create_job(tid, data, actor, is_admin):
    policy = pricing_policy(tid, is_admin)
    customer_id = _int(data.get("customer_id"), 0)
    vehicle_id = _int(data.get("vehicle_id"), 0)
    quotation_id = data.get("quotation_id") or None
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        header = _header_fields(conn, data, customer_id, vehicle_id)
        if quotation_id is not None:
            quotation_id = _int(quotation_id, 0)
            quote = conn.execute("SELECT status, customer_id FROM quotations WHERE id=?", (quotation_id,)).fetchone()
            if not quote:
                raise JobCardError("Quotation not found")
            if quote["status"] != "accepted":
                raise JobCardError("Only accepted quotations can create job cards")
            if quote["customer_id"] != customer_id:
                raise JobCardError("The quotation belongs to a different customer")
            if conn.execute("SELECT 1 FROM job_cards WHERE quotation_id=?", (quotation_id,)).fetchone():
                raise JobCardError("This quotation already has a job card")
        items = _load_items(conn, data.get("items"), policy)
        subtotal, discount_total, total = _totals(items)
        shortages = stock_shortages(conn, items)
        number = _next_job_number(conn)
        cur = conn.execute("""INSERT INTO job_cards (job_number, status, customer_id, vehicle_id, customer_name,
                              customer_phone, customer_email, mileage_in, technician, quotation_id, promised_date,
                              work_performed, notes, subtotal, discount_total, total, created_by)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (number, "open", customer_id, vehicle_id, header["customer_name"], header["customer_phone"],
                            header["customer_email"], header["mileage_in"], header["technician"], quotation_id,
                            header["promised_date"], header["work_performed"], header["notes"], subtotal,
                            discount_total, total, actor or ""))
        job_id = cur.lastrowid
        _insert_items(conn, job_id, items)
        _event(conn, job_id, "created", actor, number)
        conn.commit()
        return {"id": job_id, "job_number": number, "total": total, "warnings": shortages}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_job(tid, job_id, data, actor, is_admin):
    policy = pricing_policy(tid, is_admin)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status, customer_id, vehicle_id FROM job_cards WHERE id=?", (int(job_id),)).fetchone()
        if not current:
            raise JobCardError("Job card not found")
        if current["status"] not in EDITABLE_STATUSES:
            raise JobCardError(f"A {current['status'].replace('_', ' ')} job card cannot be edited")
        header = _header_fields(conn, data, current["customer_id"], current["vehicle_id"])
        items = _load_items(conn, data.get("items"), policy)
        subtotal, discount_total, total = _totals(items)
        shortages = stock_shortages(conn, items)
        conn.execute("""UPDATE job_cards SET mileage_in=?, technician=?, promised_date=?, work_performed=?, notes=?,
                        subtotal=?, discount_total=?, total=?, updated_at=datetime('now') WHERE id=?""",
                     (header["mileage_in"], header["technician"], header["promised_date"], header["work_performed"],
                      header["notes"], subtotal, discount_total, total, int(job_id)))
        conn.execute("DELETE FROM job_card_items WHERE job_card_id=?", (int(job_id),))
        _insert_items(conn, int(job_id), items)
        _event(conn, int(job_id), "updated", actor)
        conn.commit()
        return {"id": int(job_id), "total": total, "warnings": shortages}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_status(tid, job_id, new_status, actor, can_cancel=False, mileage_out=None):
    """Move a job card through the workflow. Cancelling needs permission and is refused once invoiced."""
    if new_status not in STATUSES:
        raise JobCardError("Unknown status")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, invoice_id, job_number FROM job_cards WHERE id=?", (int(job_id),)).fetchone()
        if not row:
            raise JobCardError("Job card not found")
        if new_status not in TRANSITIONS[row["status"]]:
            raise JobCardError(f"Cannot change status from {row['status']} to {new_status}")
        if new_status == "cancelled":
            if not can_cancel:
                raise JobCardError("You are not authorised to cancel job cards")
            if row["invoice_id"] is not None:
                raise JobCardError("An invoiced job card cannot be cancelled")
        extra_sql, params = "", []
        if new_status == "completed":
            extra_sql = ", completed_at=datetime('now')"
            if mileage_out not in (None, ""):
                extra_sql += ", mileage_out=?"
                params.append(_whole_number(mileage_out, "Mileage out", 0, MAX_MILEAGE))
        conn.execute(f"UPDATE job_cards SET status=?, updated_at=datetime('now'){extra_sql} WHERE id=?",
                     [new_status, *params, int(job_id)])
        _event(conn, int(job_id), f"status:{new_status}", actor, row["job_number"])
        conn.commit()
        return {"id": int(job_id), "status": new_status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_technician(tid, job_id, technician, actor):
    name = _text(technician, "technician")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM job_cards WHERE id=?", (int(job_id),)).fetchone()
        if not row:
            raise JobCardError("Job card not found")
        if row["status"] not in ("open", "in_progress", "ready"):
            raise JobCardError("The technician can only be changed before collection")
        conn.execute("UPDATE job_cards SET technician=?, updated_at=datetime('now') WHERE id=?", (name, int(job_id)))
        _event(conn, int(job_id), "technician", actor, name)
        conn.commit()
        return {"id": int(job_id), "technician": name}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_job(tid, job_id):
    conn = get_conn(tid)
    try:
        header = conn.execute("""SELECT j.*, i.invoice_number FROM job_cards j
                                 LEFT JOIN invoices i ON i.id = j.invoice_id WHERE j.id=?""", (int(job_id),)).fetchone()
        if not header:
            return None
        items = [dict(r) for r in conn.execute("SELECT * FROM job_card_items WHERE job_card_id=? ORDER BY position",
                                               (int(job_id),)).fetchall()]
        events = [dict(r) for r in conn.execute("SELECT event, actor, detail, created_at FROM job_card_events "
                                                "WHERE job_card_id=? ORDER BY id DESC", (int(job_id),)).fetchall()]
        return {"header": dict(header), "items": items, "events": events,
                "warnings": stock_shortages(conn, items)}
    finally:
        conn.close()


def price_changes(tid, job_id):
    """Items whose current price no longer matches the agreed job price. Used before any invoicing."""
    detail = get_job(tid, job_id)
    if not detail:
        return None
    conn = get_conn(tid)
    try:
        changes = []
        for item in detail["items"]:
            if item["item_type"] == "product":
                row = conn.execute("SELECT selling_price FROM products WHERE id=?", (item["product_id"],)).fetchone()
            else:
                row = conn.execute("SELECT default_price FROM services WHERE id=?", (item["service_id"],)).fetchone()
            current = money(row[0] or 0) if row else None
            if current is None or abs(current - item["unit_price"]) > 0.005:
                changes.append({"position": item["position"], "description": item["description"],
                                "agreed": item["unit_price"], "current": current})
        return changes
    finally:
        conn.close()


def list_jobs(tid, status=None, search=None, limit=200):
    conn = get_conn(tid)
    try:
        sql = """SELECT j.id, j.job_number, j.status, j.customer_name, j.total, j.opened_at, v.registration
                 FROM job_cards j JOIN vehicles v ON v.id = j.vehicle_id WHERE 1=1"""
        params = []
        if status in STATUSES:
            sql += " AND j.status=?"
            params.append(status)
        if search:
            sql += " AND (j.job_number LIKE ? OR v.registration LIKE ? OR j.customer_name LIKE ?)"
            params += [f"%{search}%", f"%{search.upper()}%", f"%{search}%"]
        sql += " ORDER BY j.id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
