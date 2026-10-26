"""Daily Cash-Up: calculations and the records that open and close a business day.

All money here is integer cents. Floats are never used for totals, and every write runs in one SQLite transaction.

Sources, and how each one is counted:
- Sales: the tenant's completed invoices for the business date. Each invoice counts once, in its own method's
  category. Credit invoices are sales only, never money received. Job Cards count only through their invoices.
- Receipts: debtor payments for the business date. These are money received, not sales. Only cash receipts move
  expected physical cash.
- Movements: cash added, cash removed, expenses and paid-outs recorded against the open day.

Historical rows (business_date NULL) are included by their original sale or payment date, as recorded. They are
never re-dated, and they are counted separately in the summary so the limitation stays visible.
"""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date

from ..tenant_db import get_conn
from . import shop_time

MOVEMENT_KINDS = ("cash_added", "cash_removed", "expense", "paid_out")
SALE_CATEGORY = {"cash": "cash_sale", "card": "card_sale", "eft": "eft_sale", "credit": "credit_sale"}
RECEIPT_CATEGORY = {"cash": "cash_receipt", "card": "card_receipt", "bank_transfer": "eft_receipt"}
OTHER_RECEIPT = "other_receipt"
MONEY_RE = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
MAX_CENTS = 999_999_999_99


class CashUpError(ValueError):
    """A cash-up rule was broken. Safe to show to the user."""


def to_cents(value):
    """Convert a stored amount (REAL or TEXT) to integer cents, rounding half up. Never uses binary floats."""
    if value is None:
        return 0
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise CashUpError(f"Invalid stored amount: {value!r}")


def parse_money(text, field="Amount"):
    """Parse a user-entered amount such as '150' or '150.25' into integer cents. Rejects anything else."""
    value = str(text if text is not None else "").strip().replace(" ", "")
    if not MONEY_RE.match(value):
        raise CashUpError(f"{field} must be a number with at most two decimal places")
    cents = to_cents(value)
    if not (0 <= cents <= MAX_CENTS):
        raise CashUpError(f"{field} is out of range")
    return cents


def _check_date(value):
    try:
        date.fromisoformat(str(value))
    except ValueError:
        raise CashUpError("The business date must be a valid date (YYYY-MM-DD)")
    return str(value)


def expected_cash(opening, cash_sales, cash_receipts, cash_added, expenses, paid_out, cash_removed):
    """Expected closing cash, in cents. Sales and receipts add, movements subtract or add as named."""
    return opening + cash_sales + cash_receipts + cash_added - expenses - paid_out - cash_removed


def _invoice_rows(conn, business_date):
    rows = conn.execute("""SELECT id, payment_method, total, discount, business_date FROM invoices
                           WHERE status='completed' AND (business_date = ? OR (business_date IS NULL AND sale_date = ?))
                           ORDER BY id""", (business_date, business_date)).fetchall()
    result = []
    for row in rows:
        method = row["payment_method"]
        if method not in SALE_CATEGORY:
            raise CashUpError(f"Invoice {row['id']} has an unknown payment method and cannot be counted")
        result.append({"id": row["id"], "category": SALE_CATEGORY[method], "cents": to_cents(row["total"]),
                       "discount": to_cents(row["discount"]), "historical": row["business_date"] is None})
    return result


def _payment_rows(conn, business_date):
    rows = conn.execute("""SELECT id, amount_paid, payment_method, business_date FROM payment_history
                           WHERE business_date = ? OR (business_date IS NULL AND payment_date = ?)
                           ORDER BY id""", (business_date, business_date)).fetchall()
    return [{"id": r["id"], "category": RECEIPT_CATEGORY.get(r["payment_method"], OTHER_RECEIPT),
             "cents": to_cents(r["amount_paid"]), "historical": r["business_date"] is None} for r in rows]


def _movement_totals(conn, business_date):
    totals = {kind: 0 for kind in MOVEMENT_KINDS}
    for row in conn.execute("SELECT kind, amount_cents FROM cash_movements WHERE business_date = ?", (business_date,)):
        totals[row["kind"]] += int(row["amount_cents"])
    return totals


def _cash_up_row(conn, business_date):
    return conn.execute("SELECT * FROM cash_ups WHERE business_date = ?", (business_date,)).fetchone()


def _late_items(conn, cash_up_id):
    """Late transactions counted in this cash-up. Their business dates are their original, earlier dates."""
    return [dict(r) for r in conn.execute(
        "SELECT source_type, category, amount_cents AS cents, business_date FROM cash_up_lines "
        "WHERE cash_up_id = ? AND is_late = 1 ORDER BY id", (cash_up_id,)).fetchall()]


def _figures(conn, business_date, sales, receipts):
    by_sale = {c: 0 for c in SALE_CATEGORY.values()}
    for s in sales:
        by_sale[s["category"]] += s["cents"]
    by_receipt = {c: 0 for c in ("cash_receipt", "card_receipt", "eft_receipt", OTHER_RECEIPT)}
    for r in receipts:
        by_receipt[r["category"]] += r["cents"]
    movements = _movement_totals(conn, business_date)
    cu = _cash_up_row(conn, business_date)
    opening = int(cu["opening_float_cents"]) if cu else 0
    late_sales = late_receipts = late_count = 0
    if cu:
        for item in _late_items(conn, cu["id"]):
            late_count += 1
            if item["source_type"] == "invoice":
                by_sale[item["category"]] += item["cents"]
                late_sales += item["cents"]
            else:
                by_receipt[item["category"]] += item["cents"]
                late_receipts += item["cents"]
    total_sales = sum(by_sale.values())
    expected = expected_cash(opening, by_sale["cash_sale"], by_receipt["cash_receipt"], movements["cash_added"],
                             movements["expense"], movements["paid_out"], movements["cash_removed"])
    return {
        "business_date": business_date,
        "status": cu["status"] if cu else "not_opened",
        "opening_float_cents": opening,
        "cash_sales_cents": by_sale["cash_sale"],
        "card_sales_cents": by_sale["card_sale"],
        "eft_sales_cents": by_sale["eft_sale"],
        "credit_sales_cents": by_sale["credit_sale"],
        "total_sales_cents": total_sales,
        "cash_receipts_cents": by_receipt["cash_receipt"],
        "card_receipts_cents": by_receipt["card_receipt"],
        "eft_receipts_cents": by_receipt["eft_receipt"],
        "other_receipts_cents": by_receipt[OTHER_RECEIPT],
        "cash_added_cents": movements["cash_added"],
        "cash_removed_cents": movements["cash_removed"],
        "expenses_cents": movements["expense"],
        "paid_out_cents": movements["paid_out"],
        "expected_cash_cents": expected,
        "discounts_cents": sum(s["discount"] for s in sales),
        "historical_sales": sum(1 for s in sales if s["historical"]),
        "historical_receipts": sum(1 for r in receipts if r["historical"]),
        # Late transactions are included above, and shown here so they are never mistaken for this day's own sales.
        "late_transactions": late_count,
        "late_sales_cents": late_sales,
        "late_receipts_cents": late_receipts,
        "counted_cash_cents": int(cu["counted_cash_cents"]) if cu and cu["counted_cash_cents"] is not None else None,
        "variance_cents": (int(cu["variance_cents"]) if cu and cu["variance_cents"] is not None else None),
    }


def day_summary(tid, business_date):
    """Every figure for a business date, in cents. Reads only; nothing is written.

    A closed day is answered from its stored snapshot, so nothing that happened later can change it.
    """
    business_date = _check_date(business_date)
    conn = get_conn(tid)
    try:
        cu = _cash_up_row(conn, business_date)
        if cu and cu["status"] == "closed":
            return _snapshot(cu)
        return _figures(conn, business_date, _invoice_rows(conn, business_date), _payment_rows(conn, business_date))
    finally:
        conn.close()


def _snapshot(cu):
    """A closed cash-up exactly as it was stored when it was closed."""
    def i(name):
        return int(cu[name]) if cu[name] is not None else 0
    return {
        "business_date": cu["business_date"], "status": "closed",
        "opening_float_cents": i("opening_float_cents"),
        "cash_sales_cents": i("cash_sales_cents"), "card_sales_cents": i("card_sales_cents"),
        "eft_sales_cents": i("eft_sales_cents"), "credit_sales_cents": i("credit_sales_cents"),
        "total_sales_cents": i("total_sales_cents"),
        "cash_receipts_cents": i("cash_receipts_cents"), "card_receipts_cents": i("card_receipts_cents"),
        "eft_receipts_cents": i("eft_receipts_cents"), "other_receipts_cents": i("other_receipts_cents"),
        "cash_added_cents": i("cash_added_cents"), "cash_removed_cents": i("cash_removed_cents"),
        "expenses_cents": i("expenses_cents"), "paid_out_cents": i("paid_out_cents"),
        "expected_cash_cents": i("expected_cash_cents"),
        "counted_cash_cents": i("counted_cash_cents"), "variance_cents": i("variance_cents"),
        "snapshot": True,
    }


def open_day(tid, business_date, opening_float_cents, actor):
    """Open a business date with its opening float. Only one cash-up can exist for a date."""
    business_date = _check_date(business_date)
    if not (0 <= int(opening_float_cents) <= MAX_CENTS):
        raise CashUpError("The opening float is out of range")
    if not str(actor or "").strip():
        raise CashUpError("An actor is required")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _cash_up_row(conn, business_date):
            raise CashUpError(f"A cash-up for {business_date} already exists")
        conn.execute("""INSERT INTO cash_ups (business_date, status, opening_float_cents, opened_by, opened_at_utc)
                        VALUES (?, 'open', ?, ?, ?)""",
                     (business_date, int(opening_float_cents), str(actor).strip(), shop_time.utc_timestamp()))
        conn.commit()
    except CashUpError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_movement(tid, business_date, kind, amount_cents, reason, actor):
    """Record a cash movement on an open day. Movements are permanent once written."""
    business_date = _check_date(business_date)
    if kind not in MOVEMENT_KINDS:
        raise CashUpError("Unknown cash movement type")
    if not (1 <= int(amount_cents) <= MAX_CENTS):
        raise CashUpError("The movement amount must be more than zero")
    reason = str(reason or "").strip()
    actor = str(actor or "").strip()
    if not reason:
        raise CashUpError("Give a reason for the cash movement")
    if not actor:
        raise CashUpError("An actor is required")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cu = _cash_up_row(conn, business_date)
        if not cu or cu["status"] != "open":
            raise CashUpError(f"Open the cash-up for {business_date} before recording cash movements")
        conn.execute("""INSERT INTO cash_movements (business_date, kind, amount_cents, reason, actor, recorded_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (business_date, kind, int(amount_cents), reason, actor, shop_time.utc_timestamp()))
        conn.commit()
    except CashUpError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_day(tid, business_date, counted_cash_cents, actor, notes=""):
    """Close an open business date. Every source for the date is recorded as a line and counted exactly once."""
    business_date = _check_date(business_date)
    if not (0 <= int(counted_cash_cents) <= MAX_CENTS):
        raise CashUpError("The counted cash is out of range")
    actor = str(actor or "").strip()
    if not actor:
        raise CashUpError("An actor is required")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cu = _cash_up_row(conn, business_date)
        if not cu or cu["status"] != "open":
            raise CashUpError(f"There is no open cash-up for {business_date}")
        sales = _invoice_rows(conn, business_date)
        receipts = _payment_rows(conn, business_date)
        for s in sales:
            conn.execute("""INSERT INTO cash_up_lines (cash_up_id, business_date, source_type, source_id, category, amount_cents)
                            VALUES (?, ?, 'invoice', ?, ?, ?)""",
                         (cu["id"], business_date, s["id"], s["category"], s["cents"]))
        for r in receipts:
            conn.execute("""INSERT INTO cash_up_lines (cash_up_id, business_date, source_type, source_id, category, amount_cents)
                            VALUES (?, ?, 'payment', ?, ?, ?)""",
                         (cu["id"], business_date, r["id"], r["category"], r["cents"]))
        figures = _figures(conn, business_date, sales, receipts)
        expected = figures["expected_cash_cents"]
        variance = int(counted_cash_cents) - expected
        conn.execute("""UPDATE cash_ups SET status='closed', counted_cash_cents=?, expected_cash_cents=?, variance_cents=?,
                        cash_sales_cents=?, card_sales_cents=?, eft_sales_cents=?, credit_sales_cents=?, total_sales_cents=?,
                        cash_receipts_cents=?, card_receipts_cents=?, eft_receipts_cents=?, other_receipts_cents=?,
                        cash_added_cents=?, cash_removed_cents=?, expenses_cents=?, paid_out_cents=?,
                        closed_by=?, closed_at_utc=?, notes=?
                        WHERE id=?""",
                     (int(counted_cash_cents), expected, variance,
                      figures["cash_sales_cents"], figures["card_sales_cents"], figures["eft_sales_cents"],
                      figures["credit_sales_cents"], figures["total_sales_cents"],
                      figures["cash_receipts_cents"], figures["card_receipts_cents"], figures["eft_receipts_cents"],
                      figures["other_receipts_cents"], figures["cash_added_cents"], figures["cash_removed_cents"],
                      figures["expenses_cents"], figures["paid_out_cents"],
                      actor, shop_time.utc_timestamp(), str(notes or "").strip(), cu["id"]))
        conn.commit()
        figures.update({"status": "closed", "counted_cash_cents": int(counted_cash_cents), "variance_cents": variance})
        return figures
    except CashUpError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        if "UNIQUE constraint failed: cash_up_lines" in str(exc):
            raise CashUpError("A transaction for this date is already counted in another cash-up")
        raise CashUpError(f"The cash-up could not be closed: {exc}")
    finally:
        conn.close()


def list_late(tid):
    """Late transactions not yet counted in any cash-up: sales or payments dated to a closed day. Read-only."""
    conn = get_conn(tid)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cash_up_late_sources ORDER BY business_date, source_type, source_id").fetchall()]
    finally:
        conn.close()


def include_late(tid, open_date, source_type, source_id, actor):
    """Count one late transaction in an open cash-up. It keeps its original business date and is counted once.

    The closed cash-up it came from is not touched. The line records is_late=1, so the open day's totals show
    it separately, and the original date stays visible on the line.
    """
    open_date = _check_date(open_date)
    if source_type not in ("invoice", "payment"):
        raise CashUpError("Unknown transaction type")
    actor = str(actor or "").strip()
    if not actor:
        raise CashUpError("An actor is required")
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cu = _cash_up_row(conn, open_date)
        if not cu or cu["status"] != "open":
            raise CashUpError(f"Open the cash-up for {open_date} before counting a late transaction")
        if source_type == "invoice":
            row = conn.execute("SELECT id, payment_method, total, business_date, status FROM invoices WHERE id=?",
                               (int(source_id),)).fetchone()
            if not row or row["status"] != "completed":
                raise CashUpError("That sale was not found")
            if row["payment_method"] not in SALE_CATEGORY:
                raise CashUpError("That sale has an unknown payment method")
            category, cents, original = SALE_CATEGORY[row["payment_method"]], to_cents(row["total"]), row["business_date"]
        else:
            row = conn.execute("SELECT id, amount_paid, payment_method, business_date FROM payment_history WHERE id=?",
                               (int(source_id),)).fetchone()
            if not row:
                raise CashUpError("That payment was not found")
            category = RECEIPT_CATEGORY.get(row["payment_method"], OTHER_RECEIPT)
            cents, original = to_cents(row["amount_paid"]), row["business_date"]
        if original is None:
            raise CashUpError("This transaction has no recorded business date, so it cannot be reconciled late")
        if original == open_date:
            raise CashUpError("This transaction already belongs to the open day")
        closed = conn.execute("SELECT 1 FROM cash_ups WHERE business_date = ? AND status = 'closed'",
                              (original,)).fetchone()
        if not closed:
            raise CashUpError("This transaction is not on a closed day, so it is not late")
        conn.execute("""INSERT INTO cash_up_lines (cash_up_id, business_date, source_type, source_id, category,
                        amount_cents, is_late) VALUES (?, ?, ?, ?, ?, ?, 1)""",
                     (cu["id"], original, source_type, int(source_id), category, cents))
        conn.commit()
        return {"source_type": source_type, "source_id": int(source_id), "business_date": original,
                "category": category, "amount_cents": cents, "counted_in": open_date}
    except CashUpError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        if "UNIQUE constraint failed" in str(exc):
            raise CashUpError("This transaction is already counted in a cash-up")
        raise CashUpError(f"The late transaction could not be counted: {exc}")
    finally:
        conn.close()
