"""Phase 2A financial foundation: shop timezone, business dates, UTC record times, and payment idempotency."""
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.main_db import create_tenant
from app.services import shop_time
from app.tenant_db import add_debtor, add_payment, add_product, complete_sale, get_conn, get_debtor, init_tenant_db


@pytest.fixture
def shop():
    username = "fin_" + uuid4().hex[:10]
    tid, err = create_tenant("Finance Shop", username, "fin@example.com", "fin-password-123")
    assert not err
    init_tenant_db(tid)
    product = add_product(tid, "TYRE-F", "Finance Tyre", current_stock=10, selling_price=100)
    debtor = add_debtor(tid, "Debtor One", "", "debtor@example.com", 1000, "2026-09-09", "email", 28, "")
    return {"tid": tid, "product": product, "debtor": debtor}


@pytest.fixture
def late_evening(monkeypatch):
    """22:30 UTC on 26 October is 00:30 on 27 October in Johannesburg: the boundary the old code got wrong."""
    moment = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(shop_time, "utc_now", lambda: moment)
    return moment


# ── Timezone ─────────────────────────────────────────────────────────────────────

def test_default_zone_is_johannesburg_and_boundary_is_correct(late_evening):
    assert shop_time.DEFAULT_SHOP_TIMEZONE == "Africa/Johannesburg"
    assert shop_time.business_date_for(late_evening) == "2026-10-27"
    assert shop_time.business_date_for(datetime(2026, 10, 26, 21, 30, tzinfo=timezone.utc)) == "2026-10-26"


def test_utc_timestamp_is_stored_in_utc(late_evening):
    assert shop_time.utc_timestamp() == "2026-10-26 22:30:00"


def test_tenant_timezone_setting_is_honoured(shop):
    conn = get_conn(shop["tid"])
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (shop_time.SHOP_TIMEZONE_SETTING, "Europe/London"))
        conn.commit()
        assert shop_time.shop_timezone_name(conn) == "Europe/London"
    finally:
        conn.close()


def test_invalid_timezone_setting_is_refused(shop):
    conn = get_conn(shop["tid"])
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (shop_time.SHOP_TIMEZONE_SETTING, "Not/AZone"))
        conn.commit()
        with pytest.raises(Exception):
            shop_time.shop_timezone_name(conn)
    finally:
        conn.close()


# ── New POS sales ────────────────────────────────────────────────────────────────

def test_new_cash_sale_gets_the_local_business_date_and_utc_time(shop, late_evening):
    result = complete_sale(shop["tid"], "cash-boundary-1", [{"type": "product", "id": shop["product"], "quantity": 1}],
                           payment_method="cash")
    conn = get_conn(shop["tid"])
    try:
        row = conn.execute("SELECT sale_date, business_date, recorded_at_utc, created_at FROM invoices WHERE id=?",
                           (result["invoice_id"],)).fetchone()
    finally:
        conn.close()
    assert row["sale_date"] == "2026-10-27"
    assert row["business_date"] == "2026-10-27"
    assert row["recorded_at_utc"] == "2026-10-26 22:30:00"


def test_explicit_sale_date_is_kept(shop, late_evening):
    result = complete_sale(shop["tid"], "explicit-date-1", [{"type": "product", "id": shop["product"], "quantity": 1}],
                           sale_date="2026-10-20", payment_method="cash")
    conn = get_conn(shop["tid"])
    try:
        row = conn.execute("SELECT sale_date, business_date FROM invoices WHERE id=?", (result["invoice_id"],)).fetchone()
    finally:
        conn.close()
    assert row["sale_date"] == "2026-10-20" and row["business_date"] == "2026-10-20"


# ── Debtor payments ──────────────────────────────────────────────────────────────

def test_payment_records_business_date_and_utc_time(shop, late_evening):
    add_payment(shop["tid"], shop["debtor"], 100, payment_method="cash")
    conn = get_conn(shop["tid"])
    try:
        row = conn.execute("SELECT payment_date, business_date, recorded_at_utc FROM payment_history").fetchone()
    finally:
        conn.close()
    assert row["payment_date"] == "2026-10-27"
    assert row["business_date"] == "2026-10-27"
    assert row["recorded_at_utc"] == "2026-10-26 22:30:00"


def test_repeat_submission_with_the_same_key_is_recorded_once(shop):
    add_payment(shop["tid"], shop["debtor"], 250, payment_date="2026-10-20", idempotency_key="pay-key-1")
    add_payment(shop["tid"], shop["debtor"], 250, payment_date="2026-10-20", idempotency_key="pay-key-1")
    assert get_debtor(shop["tid"], shop["debtor"])["total_paid_to_date"] == 250
    conn = get_conn(shop["tid"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM payment_history").fetchone()[0] == 1
    finally:
        conn.close()


def test_repeat_after_the_debt_is_cleared_is_not_an_overpayment(shop):
    add_payment(shop["tid"], shop["debtor"], 1000, payment_date="2026-10-20", idempotency_key="pay-full")
    assert add_payment(shop["tid"], shop["debtor"], 1000, payment_date="2026-10-20", idempotency_key="pay-full") is True
    assert get_debtor(shop["tid"], shop["debtor"])["total_paid_to_date"] == 1000


def test_different_keys_are_separate_payments(shop):
    add_payment(shop["tid"], shop["debtor"], 100, payment_date="2026-10-20", idempotency_key="pay-a")
    add_payment(shop["tid"], shop["debtor"], 100, payment_date="2026-10-20", idempotency_key="pay-b")
    assert get_debtor(shop["tid"], shop["debtor"])["total_paid_to_date"] == 200


def test_payments_without_a_key_keep_the_existing_behaviour(shop):
    add_payment(shop["tid"], shop["debtor"], 100, payment_date="2026-10-20")
    add_payment(shop["tid"], shop["debtor"], 100, payment_date="2026-10-20")
    assert get_debtor(shop["tid"], shop["debtor"])["total_paid_to_date"] == 200


def test_database_refuses_a_duplicate_key_even_outside_the_function(shop):
    add_payment(shop["tid"], shop["debtor"], 50, payment_date="2026-10-20", idempotency_key="pay-db")
    conn = get_conn(shop["tid"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO payment_history (debtor_id, amount_paid, payment_date, idempotency_key) "
                         "VALUES (?, 50, '2026-10-20', 'pay-db')", (shop["debtor"],))
    finally:
        conn.close()


def test_a_key_is_scoped_to_its_own_tenant(shop):
    username = "other_" + uuid4().hex[:10]
    other, _ = create_tenant("Other Shop", username, "other@example.com", "other-password-123")
    init_tenant_db(other)
    other_debtor = add_debtor(other, "Other Debtor", "", "x@example.com", 500, "2026-09-09", "email", 28, "")
    add_payment(shop["tid"], shop["debtor"], 10, payment_date="2026-10-20", idempotency_key="shared-key")
    add_payment(other, other_debtor, 20, payment_date="2026-10-20", idempotency_key="shared-key")
    assert get_debtor(other, other_debtor)["total_paid_to_date"] == 20


# ── Migration 30 ─────────────────────────────────────────────────────────────────

def test_migration_30_keeps_history_as_it_was(tmp_path, monkeypatch):
    from app.db_migrations import migrate_tenant_db, LATEST_SCHEMA_VERSION
    assert LATEST_SCHEMA_VERSION >= 30
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "77.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(77, str(path))
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO debtors (name, amount_owed, date_of_purchase) VALUES ('History', 300, '2026-09-01')")
    # A payment written before migration 30: no business date, no UTC time, no key.
    conn.execute("INSERT INTO payment_history (debtor_id, amount_paid, payment_date) VALUES (1, 300, '2026-09-02')")
    conn.execute("INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, total, payment_method, payment_status) "
                 "VALUES ('OLD-1', 'old-key', '2026-09-03', 'completed', 300, 'cash', 'paid')")
    conn.execute("DELETE FROM schema_migrations WHERE version >= 30")
    conn.commit()
    conn.close()

    migrate_tenant_db(77, str(path))  # migration 30 runs again on the existing data

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 30").fetchone()[0] == 1
    old_payment = conn.execute("SELECT amount_paid, payment_date, business_date, recorded_at_utc, idempotency_key "
                               "FROM payment_history").fetchone()
    old_invoice = conn.execute("SELECT sale_date, total, business_date, recorded_at_utc FROM invoices").fetchone()
    assert old_payment == (300.0, "2026-09-02", None, None, None)
    assert old_invoice == ("2026-09-03", 300.0, None, None)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
