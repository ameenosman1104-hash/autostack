"""Quotation engine: pricing rules, sequential numbering, status transitions and audit events."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..tenant_db import get_conn, get_setting

STATUSES = ("draft", "sent", "accepted", "rejected", "expired", "converted")
# 'converted' is set only by convert_to_sale, never by the manual status endpoint.
TRANSITIONS = {
    "draft": {"sent"},
    "sent": {"accepted", "rejected"},
    "accepted": set(),
    "rejected": set(),
    "expired": set(),
    "converted": set(),
}
CONVERSION_PAYMENT_METHODS = ("cash", "card", "eft")
EDITABLE_STATUSES = {"draft"}
MAX_QUANTITY = 100000
MAX_VALIDITY_DAYS = 365
DEFAULT_VALIDITY_DAYS = 14
DEFAULT_MAX_DISCOUNT = 10.0


class QuotationError(ValueError):
    """Validation or workflow error that is safe to show to the user."""


def money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _setting_float(tid, key, default):
    try:
        return float(get_setting(tid, key, str(default)))
    except (TypeError, ValueError):
        return float(default)


def pricing_policy(tid, is_admin):
    """Read settings before any write transaction opens, so no connections nest."""
    return {
        "max_discount": 100.0 if is_admin else _setting_float(tid, "quote_max_discount_pct", DEFAULT_MAX_DISCOUNT),
        "validity_days": int(_setting_float(tid, "quote_validity_days", DEFAULT_VALIDITY_DAYS)),
        "is_admin": bool(is_admin),
    }


def _parse_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise QuotationError(f"{field} must be a valid date (YYYY-MM-DD)")


def _header(conn, data, policy):
    customer_id = data.get("customer_id") or None
    if customer_id is not None:
        row = conn.execute("SELECT name, phone, email FROM customers WHERE id=?", (int(customer_id),)).fetchone()
        if not row:
            raise QuotationError("Customer not found")
        name, phone, email = row["name"], row["phone"] or "", row["email"] or ""
    else:
        name = (data.get("customer_name") or "").strip()
        phone = (data.get("customer_phone") or "").strip()
        email = (data.get("customer_email") or "").strip()
    if not name:
        raise QuotationError("Customer name is required")
    quote_date = _parse_date(data.get("quote_date") or date.today().isoformat(), "Quote date")
    expiry_raw = data.get("expiry_date")
    expiry = _parse_date(expiry_raw, "Expiry date") if expiry_raw else quote_date + timedelta(days=policy["validity_days"])
    if expiry < quote_date:
        raise QuotationError("Expiry date cannot be before the quote date")
    if (expiry - quote_date).days > MAX_VALIDITY_DAYS:
        raise QuotationError(f"Expiry cannot be more than {MAX_VALIDITY_DAYS} days after the quote date")
    return {
        "customer_id": int(customer_id) if customer_id is not None else None,
        "customer_name": name[:200],
        "customer_phone": phone[:50],
        "customer_email": email[:200],
        "quote_date": quote_date.isoformat(),
        "expiry_date": expiry.isoformat(),
        "notes": str(data.get("notes") or "")[:2000],
        "terms": str(data.get("terms") or "")[:4000],
    }


def _number(value, field, lo, hi):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise QuotationError(f"{field} must be a number")
    if not (lo <= number <= hi):
        raise QuotationError(f"{field} must be between {lo} and {hi}")
    return number


def _items(conn, raw_items, policy):
    if not raw_items:
        raise QuotationError("Add at least one item")
    rows = []
    for index, raw in enumerate(raw_items, start=1):
        kind = raw.get("item_type")
        qty = _number(raw.get("quantity"), "Quantity", 0.0001, MAX_QUANTITY)
        discount = _number(raw.get("discount_pct", 0) or 0, "Discount", 0, 100)
        if discount > policy["max_discount"]:
            raise QuotationError(f"Discount of {discount:g}% exceeds your limit of {policy['max_discount']:g}%")
        product_id = service_id = None
        detail = ""
        if kind == "product":
            product = conn.execute(
                "SELECT id, code, name, brand, tyre_size, pattern, selling_price FROM products WHERE id=? AND deleted=0",
                (int(raw.get("product_id") or 0),)).fetchone()
            if not product:
                raise QuotationError(f"Product not found on line {index}")
            product_id = product["id"]
            base = float(product["selling_price"] or 0)
            description = product["name"] or product["code"]
            detail = " ".join(part for part in (product["brand"], product["tyre_size"], product["pattern"]) if part)
        elif kind == "service":
            service = conn.execute(
                "SELECT id, name, description, default_price FROM services WHERE id=? AND is_active=1",
                (int(raw.get("service_id") or 0),)).fetchone()
            if not service:
                raise QuotationError(f"Service not found on line {index}")
            service_id = service["id"]
            base = float(service["default_price"] or 0)
            description = service["name"]
            detail = service["description"] or ""
        elif kind == "custom":
            description = str(raw.get("description") or "").strip()
            if not description:
                raise QuotationError(f"Description is required on line {index}")
            base = 0.0
            detail = str(raw.get("detail") or "")
        else:
            raise QuotationError(f"Unknown item type on line {index}")

        price = _number(raw.get("unit_price", base), "Unit price", 0, 10_000_000)
        if kind != "custom" and abs(price - base) > 0.005 and not policy["is_admin"]:
            raise QuotationError(f"You are not authorised to change the price on line {index}")
        line_total = money(qty * price * (1 - discount / 100))
        rows.append({
            "position": index, "item_type": kind, "product_id": product_id, "service_id": service_id,
            "description": description[:200], "detail": detail[:500], "quantity": qty,
            "base_price": money(base), "unit_price": money(price), "discount_pct": discount,
            "line_total": line_total,
        })
    return rows


def _totals(items):
    subtotal = money(sum(i["quantity"] * i["unit_price"] for i in items))
    total = money(sum(i["line_total"] for i in items))
    return {"subtotal": subtotal, "discount_total": money(subtotal - total), "total": total}


def _next_number(conn):
    row = conn.execute("SELECT next_number FROM quotation_sequence WHERE id=1").fetchone()
    value = int(row[0])
    conn.execute("UPDATE quotation_sequence SET next_number=? WHERE id=1", (value + 1,))
    return f"QUO-{value:06d}"


def _event(conn, quotation_id, event, actor, detail=""):
    conn.execute("INSERT INTO quotation_events (quotation_id, event, actor, detail) VALUES (?,?,?,?)",
                 (quotation_id, event, actor or "", detail))


def _insert_items(conn, quotation_id, items):
    for i in items:
        conn.execute("""INSERT INTO quotation_items (quotation_id, position, item_type, product_id, service_id,
                        description, detail, quantity, base_price, unit_price, discount_pct, line_total)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (quotation_id, i["position"], i["item_type"], i["product_id"], i["service_id"],
                      i["description"], i["detail"], i["quantity"], i["base_price"], i["unit_price"],
                      i["discount_pct"], i["line_total"]))


def create_quotation(tid, data, actor, is_admin):
    policy = pricing_policy(tid, is_admin)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        header = _header(conn, data, policy)
        items = _items(conn, data.get("items"), policy)
        totals = _totals(items)
        number = _next_number(conn)
        cur = conn.execute("""INSERT INTO quotations (quotation_number, status, customer_id, customer_name,
                              customer_phone, customer_email, quote_date, expiry_date, subtotal, discount_total,
                              total, notes, terms, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (number, "draft", header["customer_id"], header["customer_name"], header["customer_phone"],
                            header["customer_email"], header["quote_date"], header["expiry_date"], totals["subtotal"],
                            totals["discount_total"], totals["total"], header["notes"], header["terms"], actor or ""))
        quotation_id = cur.lastrowid
        _insert_items(conn, quotation_id, items)
        _event(conn, quotation_id, "created", actor, number)
        conn.commit()
        return {"id": quotation_id, "quotation_number": number, "total": totals["total"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_quotation(tid, quotation_id, data, actor, is_admin):
    policy = pricing_policy(tid, is_admin)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM quotations WHERE id=?", (quotation_id,)).fetchone()
        if not current:
            raise QuotationError("Quotation not found")
        if current["status"] not in EDITABLE_STATUSES:
            raise QuotationError("Only draft quotations can be edited")
        header = _header(conn, data, policy)
        items = _items(conn, data.get("items"), policy)
        totals = _totals(items)
        conn.execute("""UPDATE quotations SET customer_id=?, customer_name=?, customer_phone=?, customer_email=?,
                        quote_date=?, expiry_date=?, subtotal=?, discount_total=?, total=?, notes=?, terms=?,
                        updated_at=datetime('now') WHERE id=?""",
                     (header["customer_id"], header["customer_name"], header["customer_phone"], header["customer_email"],
                      header["quote_date"], header["expiry_date"], totals["subtotal"], totals["discount_total"],
                      totals["total"], header["notes"], header["terms"], quotation_id))
        conn.execute("DELETE FROM quotation_items WHERE quotation_id=?", (quotation_id,))
        _insert_items(conn, quotation_id, items)
        _event(conn, quotation_id, "updated", actor)
        conn.commit()
        return {"id": quotation_id, "total": totals["total"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def expire_overdue(tid):
    """Mark open quotations past their expiry date. Runs in its own transaction."""
    today = date.today().isoformat()
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        overdue = conn.execute(
            "SELECT id FROM quotations WHERE status IN ('draft','sent') AND expiry_date < ?", (today,)).fetchall()
        for row in overdue:
            conn.execute("UPDATE quotations SET status='expired', updated_at=datetime('now') WHERE id=?", (row["id"],))
            _event(conn, row["id"], "expired", "system", today)
        conn.commit()
        return len(overdue)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_status(tid, quotation_id, new_status, actor):
    if new_status not in STATUSES:
        raise QuotationError("Unknown status")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, quotation_number FROM quotations WHERE id=?", (quotation_id,)).fetchone()
        if not row:
            raise QuotationError("Quotation not found")
        if new_status not in TRANSITIONS[row["status"]]:
            raise QuotationError(f"Cannot change status from {row['status']} to {new_status}")
        sent_clause = ", sent_at=datetime('now')" if new_status == "sent" else ""
        conn.execute(f"UPDATE quotations SET status=?, updated_at=datetime('now'){sent_clause} WHERE id=?",
                     (new_status, quotation_id))
        _event(conn, quotation_id, f"status:{new_status}", actor, row["quotation_number"])
        conn.commit()
        return {"id": quotation_id, "status": new_status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_quotation(tid, quotation_id):
    expire_overdue(tid)
    conn = get_conn(tid)
    try:
        header = conn.execute("SELECT * FROM quotations WHERE id=?", (quotation_id,)).fetchone()
        if not header:
            return None
        items = conn.execute("SELECT * FROM quotation_items WHERE quotation_id=? ORDER BY position", (quotation_id,)).fetchall()
        events = conn.execute("SELECT event, actor, detail, created_at FROM quotation_events WHERE quotation_id=? ORDER BY id DESC", (quotation_id,)).fetchall()
        return {"header": dict(header), "items": [dict(i) for i in items], "events": [dict(e) for e in events]}
    finally:
        conn.close()


def list_quotations(tid, status=None, search=None, limit=200):
    expire_overdue(tid)
    conn = get_conn(tid)
    try:
        sql = "SELECT id, quotation_number, status, customer_name, quote_date, expiry_date, total FROM quotations WHERE 1=1"
        params = []
        if status in STATUSES:
            sql += " AND status=?"
            params.append(status)
        if search:
            sql += " AND (quotation_number LIKE ? OR customer_name LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _mark_converted(conn, quotation_id, invoice_id, actor, number):
    """Runs inside the POS sale transaction: the link and the sale commit or roll back together."""
    cursor = conn.execute(
        "UPDATE quotations SET status='converted', converted_invoice_id=?, updated_at=datetime('now') "
        "WHERE id=? AND status='accepted'", (invoice_id, quotation_id))
    if cursor.rowcount != 1:
        raise QuotationError("This quotation changed while converting. No sale was created.")
    _event(conn, quotation_id, "converted", actor, f"{number} -> invoice {invoice_id}")


def convert_to_sale(tid, quotation_id, actor, payment_method="cash"):
    """Turn an accepted quotation into a completed POS sale using the existing transaction engine.

    Stock is checked and deducted by complete_sale only when the sale commits. The quotation is
    linked to the invoice inside that same transaction. Retries reuse one idempotency key, so a
    double-click or a repeated request returns the original invoice instead of creating another.
    """
    from ..tenant_db import complete_sale
    if payment_method not in CONVERSION_PAYMENT_METHODS:
        raise QuotationError("Payment method must be cash, card or EFT")
    detail = get_quotation(tid, quotation_id)
    if not detail:
        raise QuotationError("Quotation not found")
    header = detail["header"]
    if header["status"] == "converted":
        return {"success": True, "duplicate": True, "quotation_number": header["quotation_number"],
                "invoice_id": header["converted_invoice_id"], "invoice_number": invoice_number(tid, header["converted_invoice_id"])}
    if header["status"] != "accepted":
        raise QuotationError(f"Only accepted quotations can be converted (this one is {header['status']})")
    if header["expiry_date"] < date.today().isoformat():
        raise QuotationError("This quotation has expired. Create a new quotation.")

    lines = []
    for item in detail["items"]:
        if item["item_type"] == "product":
            lines.append({"type": "product", "id": item["product_id"], "quantity": item["quantity"]})
        elif item["item_type"] == "service":
            lines.append({"type": "service", "id": item["service_id"], "quantity": item["quantity"]})
        else:
            raise QuotationError("Custom lines cannot be converted to a sale. Replace them with products or services.")

    number = header["quotation_number"]

    def link(conn, result):
        # Quoted prices must still match. A mismatch rolls back the sale before it commits.
        if abs(result["total"] - header["total"]) > 0.005:
            raise QuotationError("Prices have changed since this quotation was issued. Create a new quotation.")
        _mark_converted(conn, quotation_id, result["invoice_id"], actor, number)

    try:
        result = complete_sale(
            tid, idempotency_key=f"quotation-{quotation_id}", items=lines,
            customer_id=header["customer_id"], payment_method=payment_method,
            discount=header["discount_total"], notes=f"From quotation {number}",
            created_by=actor, before_commit=link)
    except QuotationError:
        raise
    except ValueError as exc:
        raise QuotationError(str(exc))
    return {"success": True, "duplicate": bool(result.get("duplicate")), "quotation_number": number,
            "invoice_id": result["invoice_id"], "invoice_number": invoice_number(tid, result["invoice_id"])}


def invoice_number(tid, invoice_id):
    if not invoice_id:
        return None
    conn = get_conn(tid)
    try:
        row = conn.execute("SELECT invoice_number FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def search_customers(tid, term, limit=20):
    conn = get_conn(tid)
    try:
        like = f"%{term}%"
        rows = conn.execute("SELECT id, name, phone, email FROM customers WHERE name LIKE ? OR phone LIKE ? ORDER BY name LIMIT ?",
                            (like, like, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_customer(tid, name, phone="", email=""):
    name = (name or "").strip()
    if not name:
        raise QuotationError("Customer name is required")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("INSERT INTO customers (name, phone, email) VALUES (?,?,?)",
                           (name[:200], (phone or "")[:50], (email or "")[:200]))
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "phone": phone or "", "email": email or ""}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def search_products(tid, term, limit=25):
    conn = get_conn(tid)
    try:
        like = f"%{term}%"
        rows = conn.execute("""SELECT id, code, name, brand, tyre_size, pattern, selling_price, current_stock
                               FROM products WHERE deleted=0 AND (tyre_size LIKE ? OR brand LIKE ? OR name LIKE ? OR code LIKE ?)
                               ORDER BY brand, tyre_size LIMIT ?""", (like, like, like, like, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_services(tid):
    conn = get_conn(tid)
    try:
        rows = conn.execute("SELECT id, name, default_price FROM services WHERE is_active=1 ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
