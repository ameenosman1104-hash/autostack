"""Daily Cash-Up engine: formulas in exact cents, source protection, closed-day immutability, historical rows,
timezone boundaries, tenant isolation, and migration 31."""
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.main_db import create_tenant
from app.services import cashup as cu
from app.services import shop_time
from app.tenant_db import (add_customer, add_debtor, add_payment, add_product, complete_sale, get_conn,
                           init_tenant_db)

DAY = "2026-10-27"
PASSWORD = "cashup-engine-password"


def _tenant(name):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    return tid


@pytest.fixture
def shop():
    tid = _tenant("cashup")
    return {
        "tid": tid,
        "cash": add_product(tid, "CASH-100", "Cash item", current_stock=50, selling_price=100),
        "card": add_product(tid, "CARD-50", "Card item", current_stock=50, selling_price=50),
        "eft": add_product(tid, "EFT-30", "EFT item", current_stock=50, selling_price=30),
        "credit": add_product(tid, "CRED-200", "Credit item", current_stock=50, selling_price=200),
        "debtor": add_debtor(tid, "Account Holder", "", "holder@example.com", 5000, DAY, "email", 28, ""),
        "customer": add_customer(tid, "Credit Customer", phone="0821110000"),
    }


@pytest.fixture
def late_evening(monkeypatch):
    """22:30 UTC on 26 October is 00:30 on 27 October in Johannesburg."""
    moment = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(shop_time, "utc_now", lambda: moment)
    return moment


def _sell(shop, key, product, method, quantity=1, discount=0, sale_date=DAY):
    return complete_sale(shop["tid"], key, [{"type": "product", "id": shop[product], "quantity": quantity}],
                         payment_method=method, discount=discount, sale_date=sale_date)


def _raw(tid, sql, params=()):
    conn = get_conn(tid)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── Money arithmetic ─────────────────────────────────────────────────────────────

def test_money_is_exact_integer_cents():
    assert cu.to_cents(0.1 + 0.2) == 30
    assert cu.to_cents("18500.00") == 1_850_000
    assert cu.to_cents(None) == 0
    assert cu.parse_money("0.10") == 10 and cu.parse_money("150") == 15_000
    assert isinstance(cu.to_cents(12.34), int)


@pytest.mark.parametrize("text", ["1.234", "abc", "-5", "", "1,00", "1e3"])
def test_user_money_input_is_strict(text):
    with pytest.raises(cu.CashUpError):
        cu.parse_money(text)


def test_expected_cash_formula_matches_the_specification():
    assert cu.expected_cash(50_000, 10_000, 7_550, 4_000, 1_525, 1_000, 500) == 68_525


# ── Formula, end to end ──────────────────────────────────────────────────────────

def test_full_day_cash_up_with_every_source(shop, late_evening):
    _sell(shop, "s-cash", "cash", "cash")                    # 100.00 cash sale
    _sell(shop, "s-card", "card", "card")                    # 50.00 card sale
    _sell(shop, "s-eft", "eft", "eft")                       # 30.00 EFT sale
    complete_sale(shop["tid"], "s-credit", [{"type": "product", "id": shop["credit"], "quantity": 1}],
                  customer_id=shop["customer"], payment_method="credit", sale_date=DAY)   # 200.00 credit sale
    add_payment(shop["tid"], shop["debtor"], 75.50, payment_method="cash")         # cash receipt
    add_payment(shop["tid"], shop["debtor"], 20, payment_method="card")            # card receipt
    add_payment(shop["tid"], shop["debtor"], 10, payment_method="bank_transfer")   # EFT receipt
    cu.open_day(shop["tid"], DAY, 50_000, "manager")
    cu.add_movement(shop["tid"], DAY, "cash_added", 4_000, "Float top-up", "manager")
    cu.add_movement(shop["tid"], DAY, "expense", 1_525, "Cleaning supplies", "manager")
    cu.add_movement(shop["tid"], DAY, "paid_out", 1_000, "Courier", "manager")
    cu.add_movement(shop["tid"], DAY, "cash_removed", 500, "Bank drop", "manager")
    summary = cu.day_summary(shop["tid"], DAY)
    assert summary["cash_sales_cents"] == 10_000
    assert summary["card_sales_cents"] == 5_000
    assert summary["eft_sales_cents"] == 3_000
    assert summary["cash_receipts_cents"] == 7_550
    assert summary["card_receipts_cents"] == 2_000
    assert summary["eft_receipts_cents"] == 1_000
    assert summary["credit_sales_cents"] == 20_000
    assert summary["total_sales_cents"] == 38_000          # cash + card + EFT + credit
    assert summary["expected_cash_cents"] == 50_000 + 10_000 + 7_550 + 4_000 - 1_525 - 1_000 - 500


def test_shortage_and_surplus(shop, late_evening):
    _sell(shop, "v-cash", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    short = cu.close_day(shop["tid"], DAY, 9_950, "manager")
    assert short["expected_cash_cents"] == 10_000 and short["variance_cents"] == -50
    assert cu.day_summary(shop["tid"], DAY)["variance_cents"] == -50


def test_surplus_is_positive(shop, late_evening):
    _sell(shop, "p-cash", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    assert cu.close_day(shop["tid"], DAY, 10_025, "manager")["variance_cents"] == 25


def test_credit_sales_are_sales_not_cash(shop, late_evening):
    complete_sale(shop["tid"], "credit-only", [{"type": "product", "id": shop["credit"], "quantity": 1}],
                  customer_id=shop["customer"], payment_method="credit", sale_date=DAY)
    cu.open_day(shop["tid"], DAY, 1_000, "manager")
    result = cu.day_summary(shop["tid"], DAY)
    assert result["credit_sales_cents"] == 20_000
    assert result["total_sales_cents"] == 20_000
    assert result["cash_receipts_cents"] == 0
    assert result["expected_cash_cents"] == 1_000


def test_credit_sale_later_paid_in_cash_counts_once_on_each_side(shop, late_evening):
    complete_sale(shop["tid"], "credit-later", [{"type": "product", "id": shop["credit"], "quantity": 1}],
                  customer_id=shop["customer"], payment_method="credit", sale_date=DAY)
    debtor = _raw(shop["tid"], "SELECT id FROM debtors ORDER BY id DESC LIMIT 1")[0][0]
    add_payment(shop["tid"], debtor, 200, payment_method="cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    result = cu.day_summary(shop["tid"], DAY)
    assert result["credit_sales_cents"] == 20_000        # the sale, once
    assert result["cash_receipts_cents"] == 20_000       # the receipt, once
    assert result["total_sales_cents"] == 20_000         # receipts never add to sales
    assert result["expected_cash_cents"] == 20_000


def test_discount_is_taken_from_the_invoice_total_once(shop, late_evening):
    _sell(shop, "disc-1", "cash", "cash", discount=10)
    cu.open_day(shop["tid"], DAY, 0, "manager")
    result = cu.day_summary(shop["tid"], DAY)
    assert result["cash_sales_cents"] == 9_000
    assert result["discounts_cents"] == 1_000


def test_duplicate_payment_submission_is_counted_once(shop, late_evening):
    add_payment(shop["tid"], shop["debtor"], 100, payment_method="cash", idempotency_key="dup-pay")
    add_payment(shop["tid"], shop["debtor"], 100, payment_method="cash", idempotency_key="dup-pay")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    assert cu.day_summary(shop["tid"], DAY)["cash_receipts_cents"] == 10_000


def test_job_card_conversion_is_counted_through_its_invoice_only(shop, late_evening):
    from app.services import job_cards as jc
    from app.services import job_conversion as jconv
    from app.tenant_db import add_customer
    customer = add_customer(shop["tid"], "Job Customer", phone="0829990000")
    vehicle = jc.upsert_vehicle(shop["tid"], customer, "JOB10001", make="VW", model="Polo")["id"]
    job = jc.create_job(shop["tid"], {"customer_id": customer, "vehicle_id": vehicle,
                                      "items": [{"item_type": "product", "product_id": shop["cash"], "quantity": 1}]},
                        "clerk", True)
    for status in ("in_progress", "ready"):
        jc.set_status(shop["tid"], job["id"], status, "clerk")
    jconv.convert_job(shop["tid"], job["id"], "clerk", "cash", can_convert=True, is_admin=True)
    cu.open_day(shop["tid"], DAY, 0, "manager")
    result = cu.day_summary(shop["tid"], DAY)
    assert result["cash_sales_cents"] == 10_000
    assert result["total_sales_cents"] == 10_000


# ── Source protection ────────────────────────────────────────────────────────────

def test_one_open_cash_up_per_date(shop, late_evening):
    cu.open_day(shop["tid"], DAY, 0, "manager")
    with pytest.raises(cu.CashUpError, match="already exists"):
        cu.open_day(shop["tid"], DAY, 0, "manager")


def test_a_source_cannot_be_counted_in_two_cash_ups(shop, late_evening):
    _sell(shop, "twice", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.close_day(shop["tid"], DAY, 10_000, "manager")
    invoice = _raw(shop["tid"], "SELECT id FROM invoices")[0][0]
    cu.open_day(shop["tid"], "2026-10-28", 0, "manager")
    conn = get_conn(shop["tid"])
    try:
        open_id = conn.execute("SELECT id FROM cash_ups WHERE business_date='2026-10-28'").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO cash_up_lines (cash_up_id, business_date, source_type, source_id, category, amount_cents) "
                         "VALUES (?, '2026-10-28', 'invoice', ?, 'cash_sale', 10000)", (open_id, invoice))
    finally:
        conn.close()


def test_movements_need_an_open_cash_up(shop, late_evening):
    with pytest.raises(cu.CashUpError, match="Open the cash-up"):
        cu.add_movement(shop["tid"], DAY, "expense", 100, "No float", "manager")


def test_movements_need_a_reason_and_an_actor(shop, late_evening):
    cu.open_day(shop["tid"], DAY, 0, "manager")
    with pytest.raises(cu.CashUpError, match="reason"):
        cu.add_movement(shop["tid"], DAY, "expense", 100, "   ", "manager")
    with pytest.raises(cu.CashUpError, match="actor"):
        cu.add_movement(shop["tid"], DAY, "expense", 100, "Reason", "")


def test_closed_day_cannot_be_changed(shop, late_evening):
    _sell(shop, "frozen", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.close_day(shop["tid"], DAY, 10_000, "manager")
    conn = get_conn(shop["tid"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE cash_ups SET counted_cash_cents = 1 WHERE business_date = ?", (DAY,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM cash_ups WHERE business_date = ?", (DAY,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE cash_up_lines SET amount_cents = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE invoices SET total = 1 WHERE business_date = ?", (DAY,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM invoices WHERE business_date = ?", (DAY,))
    finally:
        conn.close()
    with pytest.raises(cu.CashUpError):
        cu.add_movement(shop["tid"], DAY, "expense", 100, "Late", "manager")


def test_closed_day_still_accepts_late_debtor_payment_and_identifies_it(shop, late_evening):
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.close_day(shop["tid"], DAY, 0, "manager")
    add_payment(shop["tid"], shop["debtor"], 50, payment_date=DAY, payment_method="cash")
    late = [r for r in cu.list_late(shop["tid"]) if r["source_type"] == "payment"]
    assert len(late) == 1 and late[0]["business_date"] == DAY and late[0]["amount"] == 50


def test_closed_summary_is_stored_and_consistent(shop, late_evening):
    _sell(shop, "stored", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 1_000, "manager")
    cu.add_movement(shop["tid"], DAY, "expense", 250, "Tea", "manager")
    closed = cu.close_day(shop["tid"], DAY, 10_750, "manager")
    row = _raw(shop["tid"], "SELECT * FROM cash_ups WHERE business_date = ?", (DAY,))[0]
    assert row["status"] == "closed"
    assert row["expected_cash_cents"] == closed["expected_cash_cents"] == 10_750
    assert row["variance_cents"] == 0 == row["counted_cash_cents"] - row["expected_cash_cents"]


# ── Dates and history ────────────────────────────────────────────────────────────

def test_boundary_sale_belongs_to_the_next_business_day(shop, late_evening):
    complete_sale(shop["tid"], "edge", [{"type": "product", "id": shop["cash"], "quantity": 1}], payment_method="cash")
    assert cu.day_summary(shop["tid"], "2026-10-26")["cash_sales_cents"] == 0
    assert cu.day_summary(shop["tid"], "2026-10-27")["cash_sales_cents"] == 10_000


def test_historical_rows_are_counted_by_their_original_date_and_flagged(shop, late_evening):
    conn = get_conn(shop["tid"])
    try:
        conn.execute("""INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, subtotal, discount, tax,
                        total, payment_method, payment_status, created_by) VALUES ('OLD-1', 'legacy-key', '2026-09-10',
                        'completed', 300, 0, 0, 300, 'cash', 'paid', 'old')""")
        conn.execute("INSERT INTO payment_history (debtor_id, amount_paid, payment_date) VALUES (?, 120, '2026-09-10')",
                     (shop["debtor"],))
        conn.commit()
    finally:
        conn.close()
    summary = cu.day_summary(shop["tid"], "2026-09-10")
    assert summary["cash_sales_cents"] == 30_000
    assert summary["cash_receipts_cents"] == 12_000
    assert summary["historical_sales"] == 1 and summary["historical_receipts"] == 1


def test_historical_rows_are_never_rewritten(shop, late_evening):
    conn = get_conn(shop["tid"])
    try:
        conn.execute("""INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, subtotal, discount, tax,
                        total, payment_method, payment_status, created_by) VALUES ('OLD-2', 'legacy-2', '2026-09-11',
                        'completed', 50, 0, 0, 50, 'cash', 'paid', 'old')""")
        conn.commit()
    finally:
        conn.close()
    cu.open_day(shop["tid"], "2026-09-11", 0, "manager")
    cu.close_day(shop["tid"], "2026-09-11", 5_000, "manager")
    row = _raw(shop["tid"], "SELECT sale_date, business_date, recorded_at_utc FROM invoices WHERE invoice_number='OLD-2'")[0]
    assert row["sale_date"] == "2026-09-11" and row["business_date"] is None and row["recorded_at_utc"] is None


# ── Tenant isolation ─────────────────────────────────────────────────────────────

def test_cash_up_is_isolated_by_tenant(shop, late_evening):
    other = _tenant("other")
    _sell(shop, "mine", "cash", "cash")
    assert cu.day_summary(other, DAY)["cash_sales_cents"] == 0
    cu.open_day(other, DAY, 0, "other-manager")         # the same date is allowed in another tenant
    assert cu.day_summary(shop["tid"], DAY)["status"] == "not_opened"


# ── Migration 31 ─────────────────────────────────────────────────────────────────

def test_migration_31_adds_tables_and_is_repeatable(tmp_path, monkeypatch):
    from app.db_migrations import migrate_tenant_db, LATEST_SCHEMA_VERSION
    assert LATEST_SCHEMA_VERSION == 31
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "91.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(91, str(path))
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_migrations WHERE version >= 31")
    conn.execute("DROP TABLE IF EXISTS cash_up_lines")
    conn.execute("DROP TABLE IF EXISTS cash_movements")
    conn.execute("DROP TABLE IF EXISTS cash_ups")
    conn.execute("INSERT INTO customers (name) VALUES ('Kept')")
    conn.commit()
    conn.close()
    migrate_tenant_db(91, str(path))
    migrate_tenant_db(91, str(path))
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"cash_ups", "cash_movements", "cash_up_lines"} <= tables
    assert "cash_ups_closed_no_update" in triggers and "invoices_counted_no_update" in triggers
    assert "cash_up_lines_late_flag" in triggers
    assert "invoices_closed_day_no_insert" not in triggers, "closed days must not block new sales or payments"
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "cash_up_late_sources" in views
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 31
    assert conn.execute("SELECT COUNT(*) FROM customers WHERE name='Kept'").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


# ── Late transactions after a cash-up closes ────────────────────────────────────

NEXT = "2026-10-28"


def _closed_day_with_sale(shop, key="closing-sale"):
    _sell(shop, key, "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    return cu.close_day(shop["tid"], DAY, 10_000, "manager")


def test_sale_after_closure_is_allowed_and_identified(shop, late_evening):
    closed = _closed_day_with_sale(shop)
    late = _sell(shop, "after-close", "card", "card", sale_date=DAY)
    assert late["success"] is True
    listed = [r for r in cu.list_late(shop["tid"]) if r["source_type"] == "invoice"]
    assert [r["source_id"] for r in listed] == [late["invoice_id"]]
    row = _raw(shop["tid"], "SELECT business_date, sale_date, recorded_at_utc FROM invoices WHERE id=?",
               (late["invoice_id"],))[0]
    assert row["business_date"] == DAY, "the late sale keeps its original business date"
    assert row["recorded_at_utc"] == "2026-10-26 22:30:00"
    assert cu.day_summary(shop["tid"], DAY)["expected_cash_cents"] == closed["expected_cash_cents"]


def test_debtor_payment_after_closure_is_allowed_and_identified(shop, late_evening):
    _closed_day_with_sale(shop)
    add_payment(shop["tid"], shop["debtor"], 40, payment_date=DAY, payment_method="eft")
    listed = [r for r in cu.list_late(shop["tid"]) if r["source_type"] == "payment"]
    assert len(listed) == 1 and listed[0]["business_date"] == DAY and listed[0]["method"] == "eft"


def test_closed_snapshot_never_changes_after_late_transactions(shop, late_evening):
    _closed_day_with_sale(shop)
    before = cu.day_summary(shop["tid"], DAY)
    _sell(shop, "late-1", "cash", "cash", sale_date=DAY)
    add_payment(shop["tid"], shop["debtor"], 25, payment_date=DAY, payment_method="cash")
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.close_day(shop["tid"], NEXT, 0, "manager")
    after = cu.day_summary(shop["tid"], DAY)
    assert after == before
    assert after["snapshot"] is True


def test_late_transaction_is_counted_once_in_an_open_cash_up(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "counted-late", "cash", "cash", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 5_000, "manager")
    counted = cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    assert counted["business_date"] == DAY and counted["counted_in"] == NEXT
    today = cu.day_summary(shop["tid"], NEXT)
    assert today["late_transactions"] == 1 and today["late_sales_cents"] == 10_000
    assert today["cash_sales_cents"] == 10_000, "the late sale is counted in the cash-up that holds it"
    assert all(r["source_id"] != late["invoice_id"] for r in cu.list_late(shop["tid"]))


def test_late_transaction_cannot_be_counted_twice(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "twice-late", "cash", "cash", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    with pytest.raises(cu.CashUpError, match="already counted"):
        cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    cu.open_day(shop["tid"], "2026-10-29", 0, "manager")
    with pytest.raises(cu.CashUpError, match="already counted"):
        cu.include_late(shop["tid"], "2026-10-29", "invoice", late["invoice_id"], "manager")


def test_late_payment_is_counted_once_with_its_original_date(shop, late_evening):
    _closed_day_with_sale(shop)
    add_payment(shop["tid"], shop["debtor"], 60, payment_date=DAY, payment_method="cash")
    payment_id = _raw(shop["tid"], "SELECT id FROM payment_history ORDER BY id DESC LIMIT 1")[0][0]
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.include_late(shop["tid"], NEXT, "payment", payment_id, "manager")
    summary = cu.day_summary(shop["tid"], NEXT)
    assert summary["cash_receipts_cents"] == 6_000 and summary["late_receipts_cents"] == 6_000
    line = _raw(shop["tid"], "SELECT business_date, is_late FROM cash_up_lines WHERE source_type='payment' AND source_id=?",
                (payment_id,))[0]
    assert line["business_date"] == DAY and line["is_late"] == 1


def test_late_transaction_cannot_be_counted_into_a_closed_cash_up(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "closed-target", "cash", "cash", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.close_day(shop["tid"], NEXT, 0, "manager")
    with pytest.raises(cu.CashUpError, match="Open the cash-up"):
        cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")


def test_a_sale_on_an_open_day_is_not_late(shop, late_evening):
    _sell(shop, "open-day-sale", "cash", "cash")          # dated DAY, which is still open
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    sale = _raw(shop["tid"], "SELECT id FROM invoices WHERE idempotency_key='open-day-sale'")[0][0]
    with pytest.raises(cu.CashUpError, match="not on a closed day"):
        cu.include_late(shop["tid"], NEXT, "invoice", sale, "manager")


def test_counted_sources_are_immutable_even_in_an_open_cash_up(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "immutable-late", "cash", "cash", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    conn = get_conn(shop["tid"])
    try:
        with pytest.raises(sqlite3.IntegrityError, match="cannot be changed"):
            conn.execute("UPDATE invoices SET total = 1 WHERE id = ?", (late["invoice_id"],))
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM invoices WHERE id = ?", (late["invoice_id"],))
    finally:
        conn.close()


def test_uncounted_late_sources_remain_editable(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "editable-late", "cash", "cash", sale_date=DAY)
    conn = get_conn(shop["tid"])
    try:
        conn.execute("UPDATE invoices SET notes = 'reviewed' WHERE id = ?", (late["invoice_id"],))
        conn.commit()
    finally:
        conn.close()
    assert _raw(shop["tid"], "SELECT notes FROM invoices WHERE id=?", (late["invoice_id"],))[0][0] == "reviewed"


def test_historical_rows_without_a_business_date_are_not_late(shop, late_evening):
    conn = get_conn(shop["tid"])
    try:
        conn.execute("""INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, subtotal, discount, tax,
                        total, payment_method, payment_status, created_by) VALUES ('HIST-9', 'hist-9', '2026-09-12',
                        'completed', 10, 0, 0, 10, 'cash', 'paid', 'old')""")
        conn.commit()
    finally:
        conn.close()
    cu.open_day(shop["tid"], "2026-09-12", 0, "manager")
    cu.close_day(shop["tid"], "2026-09-12", 1_000, "manager")
    assert not [r for r in cu.list_late(shop["tid"]) if r["business_date"] is None]


def test_late_transactions_are_isolated_by_tenant(shop, late_evening):
    _closed_day_with_sale(shop)
    late = _sell(shop, "isolated-late", "cash", "cash", sale_date=DAY)
    other = _tenant("latetenant")
    assert cu.list_late(other) == []
    cu.open_day(other, NEXT, 0, "other-manager")
    with pytest.raises(cu.CashUpError, match="not found"):
        cu.include_late(other, NEXT, "invoice", late["invoice_id"], "other-manager")
