"""Convert a job card into a sales invoice through the existing POS engine (complete_sale).

Price model:
- The job card keeps the AGREED prices. They are never changed by conversion.
- If a catalogue price differs from the price the customer agreed, an administrator must approve
  the new price. Each approval is appended to job_card_price_approvals (old price, new price,
  approving user, time, invoice). Approvals are written inside the sale transaction, so a failed
  or retried conversion never alters the price history.
- The invoice uses the approved final prices. The engine re-reads prices under the write lock, and
  the before_commit hook refuses the sale if its total differs from the approved total.

Atomicity: complete_sale holds the write lock from BEGIN IMMEDIATE to COMMIT. The before_commit hook
runs inside that transaction and links the invoice, the job card and the quotation, and records the
audit events. If any step fails, everything rolls back.

Duplicates: the idempotency key is 'job-card-<id>'. The job card's invoice_id is unique. Retries and
simultaneous requests return the original invoice.
"""
from ..tenant_db import complete_sale, get_conn
from . import job_cards as jc
from .quotations import QuotationError, money

PAYMENT_METHODS = ("cash", "card", "eft", "credit")
CONFIRM_REQUIRED = ("card", "eft")
ELIGIBLE_STATUSES = ("ready", "completed")
IDEMPOTENCY_PREFIX = "job-card-"
TOLERANCE = 0.005
MANUAL_NOTE = "manual staff confirmation, not bank verification"


def _current_price(conn, item):
    if item["item_type"] == "product":
        row = conn.execute("SELECT selling_price FROM products WHERE id=? AND deleted=0",
                           (item["product_id"],)).fetchone()
    else:
        row = conn.execute("SELECT default_price FROM services WHERE id=? AND is_active=1",
                           (item["service_id"],)).fetchone()
    if row is None or row[0] is None:
        raise jc.JobCardError(f"'{item['description']}' is no longer available for sale")
    return money(row[0])


def _latest_approved(conn, job_id):
    rows = conn.execute("SELECT item_position, new_price FROM job_card_price_approvals WHERE job_card_id=? "
                        "ORDER BY id", (int(job_id),)).fetchall()
    latest = {}
    for row in rows:
        latest[row["item_position"]] = float(row["new_price"])
    return latest


def plan_conversion(tid, job_id):
    """Work out the sale at current prices. Writes nothing."""
    detail = jc.get_job(tid, job_id)
    if detail is None:
        raise jc.JobCardError("Job card not found")
    conn = get_conn(tid)
    try:
        approved = _latest_approved(conn, job_id)
        lines, changes = [], []
        for item in detail["items"]:
            current = _current_price(conn, item)
            agreed = float(item["unit_price"])
            effective = approved.get(item["position"], agreed)
            qty = float(item["quantity"])
            discount = float(item["discount_pct"])
            changed = abs(current - effective) > TOLERANCE
            if changed:
                changes.append({"position": item["position"], "description": item["description"],
                                "old": effective, "new": current})
            lines.append({"position": item["position"], "item_type": item["item_type"],
                          "product_id": item["product_id"], "service_id": item["service_id"],
                          "description": item["description"], "quantity": qty, "discount_pct": discount,
                          "agreed_price": agreed, "approved_price": effective, "final_price": current,
                          "line_total": money(qty * current * (1 - discount / 100)), "changed": changed})
        subtotal = money(sum(l["quantity"] * l["final_price"] for l in lines))
        total = money(sum(l["line_total"] for l in lines))
        need = {}
        for l in lines:
            if l["item_type"] == "product":
                need[l["product_id"]] = need.get(l["product_id"], 0) + l["quantity"]
        shortages = []
        for product_id, qty in need.items():
            row = conn.execute("SELECT name, code, current_stock FROM products WHERE id=?", (product_id,)).fetchone()
            available = float(row["current_stock"] or 0)
            if available < qty:
                shortages.append({"product_id": product_id, "description": row["name"] or row["code"],
                                  "needed": qty, "available": available})
    finally:
        conn.close()
    return {"detail": detail, "lines": lines, "changes": changes, "subtotal": subtotal,
            "discount": money(subtotal - total), "total": total, "shortages": shortages}


def preview(tid, job_id, is_admin, can_convert):
    """Conversion screen data. Staff see shortage names, not stock numbers."""
    plan = plan_conversion(tid, job_id)
    shortages = plan["shortages"]
    if not is_admin:
        shortages = [{"description": s["description"]} for s in shortages]
    return {"job": plan["detail"], "lines": plan["lines"], "changes": plan["changes"],
            "subtotal": plan["subtotal"], "discount": plan["discount"], "total": plan["total"],
            "shortages": shortages, "can_convert": can_convert}


def _invoice_number(conn, invoice_id):
    row = conn.execute("SELECT invoice_number FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    return row[0] if row else None


def convert_job(tid, job_id, actor, payment_method, *, can_convert, is_admin=False,
                payment_confirmed=False, confirm_price_changes=False):
    if not can_convert:
        raise jc.JobCardError("You are not authorised to convert job cards to invoices")
    if payment_method not in PAYMENT_METHODS:
        raise jc.JobCardError("Choose cash, card, EFT or credit")
    if payment_method in CONFIRM_REQUIRED and not payment_confirmed:
        raise jc.JobCardError(f"Confirm that the {payment_method.upper()} payment was received by staff "
                              f"({MANUAL_NOTE}) before invoicing")

    detail = jc.get_job(tid, job_id)
    if detail is None:
        raise jc.JobCardError("Job card not found")
    header = detail["header"]
    if header["invoice_id"] is not None:
        conn = get_conn(tid)
        try:
            number = _invoice_number(conn, header["invoice_id"])
        finally:
            conn.close()
        return {"success": True, "duplicate": True, "job_number": header["job_number"],
                "invoice_id": header["invoice_id"], "invoice_number": number}
    if header["status"] not in ELIGIBLE_STATUSES:
        raise jc.JobCardError(f"Only ready or completed job cards can be invoiced (this one is {header['status']})")

    if header.get("quotation_id"):
        conn = get_conn(tid)
        try:
            quote = conn.execute("SELECT status FROM quotations WHERE id=?", (header["quotation_id"],)).fetchone()
        finally:
            conn.close()
        if quote is None or quote["status"] != "accepted":
            raise QuotationError("The linked quotation is no longer open. Check it before invoicing.")

    plan = plan_conversion(tid, job_id)
    if plan["changes"]:
        if not (is_admin and confirm_price_changes):
            differences = "; ".join(f"{c['description']} now R{c['new']:.2f} (was R{c['old']:.2f})"
                                    for c in plan["changes"])
            raise jc.JobCardError("Prices have changed since the job was agreed: " + differences +
                                  ". An administrator must approve the new prices before invoicing.")

    sale_items = [{"type": line["item_type"],
                   "id": line["product_id"] if line["item_type"] == "product" else line["service_id"],
                   "quantity": line["quantity"]} for line in plan["lines"]]

    note = f"Job card {header['job_number']}"
    if payment_method in CONFIRM_REQUIRED:
        note += f"; {payment_method.upper()} {MANUAL_NOTE}, confirmed by {actor}"
    expected_total = plan["total"]
    approvals = plan["changes"]

    def link(conn, result):
        if abs(result["total"] - expected_total) > TOLERANCE:
            raise jc.JobCardError("Prices or stock changed while invoicing. Nothing was saved; try again.")
        invoice_id = result["invoice_id"]
        cursor = conn.execute(
            """UPDATE job_cards SET invoice_id=?, updated_at=datetime('now')
               WHERE id=? AND invoice_id IS NULL AND status IN ('ready','completed')""",
            (invoice_id, int(job_id)))
        if cursor.rowcount != 1:
            raise jc.JobCardError("This job card changed while invoicing. Nothing was saved.")
        for change in approvals:
            conn.execute(
                """INSERT INTO job_card_price_approvals (job_card_id, item_position, description, old_price,
                   new_price, approved_by, approved_at, invoice_id)
                   VALUES (?,?,?,?,?,?,datetime('now'),?)""",
                (int(job_id), change["position"], change["description"], change["old"], change["new"], actor, invoice_id))
        if approvals:
            conn.execute("UPDATE job_cards SET price_confirmed_by=?, price_confirmed_at=datetime('now') WHERE id=?",
                         (actor, int(job_id)))
            jc._event(conn, int(job_id), "prices_approved", actor,
                      "; ".join(f"{c['description']} R{c['old']:.2f} -> R{c['new']:.2f}" for c in approvals))
        if header.get("quotation_id"):
            cursor = conn.execute("UPDATE quotations SET status='converted', converted_invoice_id=?, "
                                  "updated_at=datetime('now') WHERE id=? AND status='accepted'",
                                  (invoice_id, header["quotation_id"]))
            if cursor.rowcount != 1:
                raise QuotationError("The linked quotation changed while invoicing. Nothing was saved.")
        jc._event(conn, int(job_id), "invoiced", actor, f"{result['invoice_number']} ({payment_method})")

    try:
        result = complete_sale(
            tid, idempotency_key=f"{IDEMPOTENCY_PREFIX}{int(job_id)}", items=sale_items,
            customer_id=header["customer_id"], payment_method=payment_method,
            discount=plan["discount"], notes=note, created_by=actor, before_commit=link)
    except QuotationError:
        raise
    except ValueError as exc:
        raise jc.JobCardError(str(exc))

    if result.get("duplicate"):
        after = jc.get_job(tid, job_id)["header"]
        return {"success": True, "duplicate": True, "job_number": header["job_number"],
                "invoice_id": after["invoice_id"], "invoice_number": after["invoice_number"]}
    return {"success": True, "duplicate": False, "job_number": header["job_number"],
            "invoice_id": result["invoice_id"], "invoice_number": result["invoice_number"],
            "total": result["total"], "discount": plan["discount"], "payment_method": payment_method,
            "payment_status": result["payment_status"], "debtor_id": result.get("debtor_id"),
            "approved_changes": len(approvals)}
