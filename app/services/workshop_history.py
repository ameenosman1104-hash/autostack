"""Customer and vehicle workshop history (read-only). Tenant-scoped; invoices are only looked up, never changed."""
import re

from ..tenant_db import get_conn
from . import job_cards as jc

ORDERS = {"newest": "DESC", "oldest": "ASC"}
SEARCH_RE = re.compile(r"^[A-Za-z0-9 \-]{0,12}$")


def _order(value):
    return value if value in ORDERS else "newest"


def _id(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise jc.JobCardError(f"{label} is not valid")
    if number <= 0:
        raise jc.JobCardError(f"{label} is not valid")
    return number


def search_vehicles(tid, term, limit=50):
    """Search by registration. Input is validated; returns an empty list for an empty search."""
    text = str(term or "").strip()
    if not text:
        return []
    if not SEARCH_RE.match(text):
        raise jc.JobCardError("Enter a registration using letters, numbers, spaces or dashes")
    needle = re.sub(r"[\s\-]", "", text).upper()
    if not needle:
        raise jc.JobCardError("Enter a registration using letters or numbers")
    conn = get_conn(tid)
    try:
        rows = conn.execute("""SELECT v.id, v.registration, v.make, v.model, v.year, c.id AS customer_id, c.name AS owner
                               FROM vehicles v JOIN customers c ON c.id = v.customer_id
                               WHERE v.registration LIKE ? ORDER BY v.registration LIMIT ?""",
                            (f"%{needle}%", int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _job_lines(conn, job_ids):
    """Services performed and tyres fitted for each job, read from the job's own items."""
    counted = {jid: {"services": {}, "tyres": {}} for jid in job_ids}
    if not job_ids:
        return {jid: {"services": [], "tyres": []} for jid in job_ids}
    marks = ",".join("?" for _ in job_ids)
    rows = conn.execute(f"""SELECT job_card_id, item_type, description, detail
                            FROM job_card_items WHERE job_card_id IN ({marks}) ORDER BY position""",
                        list(job_ids)).fetchall()
    for r in rows:
        label = r["description"] + (f" ({r['detail']})" if r["detail"] else "")
        bucket = counted[r["job_card_id"]]["tyres" if r["item_type"] == "product" else "services"]
        bucket[label] = bucket.get(label, 0) + 1
    # Identical lines are grouped with a count, in the order they were first added to the job.
    return {jid: {kind: [f"{label} ×{n}" if n > 1 else label for label, n in groups.items()]
                  for kind, groups in buckets.items()}
            for jid, buckets in counted.items()}


def _jobs(conn, where_sql, params, order):
    rows = conn.execute(f"""SELECT j.id, j.job_number, j.status, j.opened_at, j.completed_at, j.mileage_in,
                                   j.mileage_out, j.technician, j.work_performed, j.total, j.invoice_id,
                                   j.vehicle_id, v.registration, i.invoice_number, i.payment_method,
                                   i.payment_status, i.total AS invoice_total
                            FROM job_cards j
                            JOIN vehicles v ON v.id = j.vehicle_id
                            LEFT JOIN invoices i ON i.id = j.invoice_id
                            WHERE {where_sql}
                            ORDER BY j.opened_at {order}, j.id {order}""", params).fetchall()
    jobs = [dict(r) for r in rows]
    lines = _job_lines(conn, [j["id"] for j in jobs])
    for job in jobs:
        job["services"] = lines[job["id"]]["services"]
        job["tyres"] = lines[job["id"]]["tyres"]
    return jobs


def vehicle_history(tid, vehicle_id, order="newest"):
    """Vehicle details, owner and every job card for the vehicle, newest first by default."""
    vid = _id(vehicle_id, "Vehicle")
    conn = get_conn(tid)
    try:
        row = conn.execute("""SELECT v.*, c.name AS owner, c.phone AS owner_phone, c.id AS customer_id
                              FROM vehicles v JOIN customers c ON c.id = v.customer_id WHERE v.id=?""", (vid,)).fetchone()
        if row is None:
            return None
        jobs = _jobs(conn, "j.vehicle_id = ?", (vid,), ORDERS[_order(order)])
        return {"vehicle": dict(row), "jobs": jobs, "order": _order(order)}
    finally:
        conn.close()


def customer_workshop_history(tid, customer_id, order="newest"):
    """Vehicles owned by the customer and every job card across them, newest first by default."""
    cid = _id(customer_id, "Customer")
    conn = get_conn(tid)
    try:
        customer = conn.execute("SELECT id, name FROM customers WHERE id=?", (cid,)).fetchone()
        if customer is None:
            return None
        vehicles = [dict(r) for r in conn.execute(
            "SELECT id, registration, make, model, year FROM vehicles WHERE customer_id=? ORDER BY registration",
            (cid,)).fetchall()]
        jobs = _jobs(conn, "j.customer_id = ?", (cid,), ORDERS[_order(order)])
        return {"customer": dict(customer), "vehicles": vehicles, "jobs": jobs, "order": _order(order)}
    finally:
        conn.close()
