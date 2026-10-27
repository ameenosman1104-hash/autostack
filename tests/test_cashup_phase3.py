"""Phase 3 Cash-Up: the financial guarantees, the service workflow and the HTTP routes (permissions, CSRF, tenants)."""
import re
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant, update_tenant
from app.services import cashup as cu
from app.services import shop_time
from app.tenant_db import (add_customer, add_debtor, add_payment, add_product, complete_sale, get_conn, init_tenant_db,
                           save_setting)

DAY = "2026-10-27"
NEXT = "2026-10-28"
PASSWORD = "cashup-phase3-password"


def _tenant(name, admin=False):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    if admin:
        update_tenant(tid, is_admin=1)
    return tid, username


@pytest.fixture
def clock(monkeypatch):
    moment = {"now": datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)}
    monkeypatch.setattr(shop_time, "utc_now", lambda: moment["now"])
    return moment


@pytest.fixture
def shop(clock):
    tid, username = _tenant("p3")
    return {
        "tid": tid, "username": username,
        "cash": add_product(tid, "P3-CASH", "Cash item", current_stock=50, selling_price=100),
        "card": add_product(tid, "P3-CARD", "Card item", current_stock=50, selling_price=50),
        "eft": add_product(tid, "P3-EFT", "EFT item", current_stock=50, selling_price=30),
        "credit": add_product(tid, "P3-CRED", "Credit item", current_stock=50, selling_price=200),
        "debtor": add_debtor(tid, "Holder", "", "h@example.com", 5000, DAY, "email", 28, ""),
        "customer": add_customer(tid, "Credit Customer", phone="0821110000"),
    }


def _sell(shop, key, product, method, sale_date=DAY):
    kwargs = {"customer_id": shop["customer"]} if method == "credit" else {}
    return complete_sale(shop["tid"], key, [{"type": "product", "id": shop[product], "quantity": 1}],
                         payment_method=method, sale_date=sale_date, **kwargs)


def _close_day_one(shop):
    """A closed earlier day, so later transactions dated to it are late."""
    _sell(shop, "earlier-sale", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.close_day(shop["tid"], DAY, 10_000, "manager")


def _raw(tid, sql, params=()):
    conn = get_conn(tid)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── H. Financial verification ────────────────────────────────────────────────────

def test_h1_late_cash_receipt_increases_expected_cash_exactly_once(shop, clock):
    _close_day_one(shop)
    add_payment(shop["tid"], shop["debtor"], 100, payment_date=DAY, payment_method="cash")
    payment = _raw(shop["tid"], "SELECT id FROM payment_history ORDER BY id DESC LIMIT 1")[0][0]
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    before = cu.day_summary(shop["tid"], NEXT)["expected_cash_cents"]
    cu.include_late(shop["tid"], NEXT, "payment", payment, "manager")
    after = cu.day_summary(shop["tid"], NEXT)["expected_cash_cents"]
    assert after - before == 10_000


def test_h2_late_card_and_eft_receipts_do_not_increase_physical_cash(shop, clock):
    _close_day_one(shop)
    add_payment(shop["tid"], shop["debtor"], 70, payment_date=DAY, payment_method="card")
    add_payment(shop["tid"], shop["debtor"], 30, payment_date=DAY, payment_method="bank_transfer")
    payments = [r[0] for r in _raw(shop["tid"], "SELECT id FROM payment_history ORDER BY id")]
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    before = cu.day_summary(shop["tid"], NEXT)
    for p in payments:
        cu.include_late(shop["tid"], NEXT, "payment", p, "manager")
    after = cu.day_summary(shop["tid"], NEXT)
    assert after["expected_cash_cents"] == before["expected_cash_cents"]
    assert after["card_receipts_cents"] == 7_000 and after["eft_receipts_cents"] == 3_000
    assert after["late_receipts_cents"] == 10_000


def test_h3_late_credit_sale_increases_sales_but_not_physical_cash(shop, clock):
    _close_day_one(shop)
    late = _sell(shop, "late-credit", "credit", "credit", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    before = cu.day_summary(shop["tid"], NEXT)
    cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    after = cu.day_summary(shop["tid"], NEXT)
    assert after["credit_sales_cents"] == before["credit_sales_cents"] + 20_000
    assert after["total_sales_cents"] == before["total_sales_cents"] + 20_000
    assert after["expected_cash_cents"] == before["expected_cash_cents"]


def test_h4_late_transactions_are_separate_from_the_days_own_activity(shop, clock):
    _close_day_one(shop)
    _sell(shop, "own-today", "cash", "cash", sale_date=NEXT)
    late = _sell(shop, "late-cash", "cash", "cash", sale_date=DAY)
    cu.open_day(shop["tid"], NEXT, 0, "manager")
    cu.include_late(shop["tid"], NEXT, "invoice", late["invoice_id"], "manager")
    summary = cu.day_summary(shop["tid"], NEXT)
    assert summary["cash_sales_cents"] == 20_000, "own sale plus late sale, each once"
    assert summary["late_sales_cents"] == 10_000 and summary["late_transactions"] == 1
    assert [r["reference"] for r in cu.late_transactions(shop["tid"])] == []


def test_h5_close_refuses_an_inconsistent_snapshot(shop, clock):
    _sell(shop, "snap", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    conn = get_conn(shop["tid"])
    try:
        conn.execute("BEGIN IMMEDIATE")
        cash_up = conn.execute("SELECT id FROM cash_ups WHERE business_date = ?", (DAY,)).fetchone()[0]
        figures = cu.day_summary(shop["tid"], DAY)
        bad = dict(figures, cash_sales_cents=figures["cash_sales_cents"] + 1)
        with pytest.raises(cu.CashUpError, match="do not match|incomplete"):
            cu._verify_snapshot(conn, cash_up, DAY, bad)
        conn.rollback()
    finally:
        conn.close()
    assert cu.cash_up_status(shop["tid"], DAY)["status"] == "open", "the check is inside the closing transaction"


def test_h6_a_closed_day_stays_accurate_after_later_transactions(shop, clock):
    _close_day_one(shop)
    before = cu.day_summary(shop["tid"], DAY)
    _sell(shop, "after-1", "cash", "cash", sale_date=DAY)
    add_payment(shop["tid"], shop["debtor"], 40, payment_date=DAY, payment_method="cash")
    assert cu.day_summary(shop["tid"], DAY) == before


# ── Closing rules ────────────────────────────────────────────────────────────────

def test_closing_twice_is_refused(shop, clock):
    _sell(shop, "twice", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    cu.close_day(shop["tid"], DAY, 10_000, "manager")
    with pytest.raises(cu.CashUpError, match="no open cash-up"):
        cu.close_day(shop["tid"], DAY, 10_000, "manager")


def test_variance_needs_an_explanation(shop, clock):
    _sell(shop, "vary", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    with pytest.raises(cu.CashUpError, match="Explain"):
        cu.close_day(shop["tid"], DAY, 9_000, "manager")
    assert cu.cash_up_status(shop["tid"], DAY)["status"] == "open", "a refused close leaves the day open"
    result = cu.close_day(shop["tid"], DAY, 9_000, "manager", variance_explanation="Short change given to a customer")
    assert result["variance_cents"] == -1_000
    assert "Short change" in cu.cash_up_status(shop["tid"], DAY)["notes"]


def test_balanced_close_needs_no_explanation(shop, clock):
    _sell(shop, "exact", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    assert cu.close_day(shop["tid"], DAY, 10_000, "manager")["variance_cents"] == 0


def test_movements_need_a_positive_amount_and_reason(shop, clock):
    cu.open_day(shop["tid"], DAY, 0, "manager")
    with pytest.raises(cu.CashUpError):
        cu.add_movement(shop["tid"], DAY, "expense", 0, "Nothing", "manager")
    with pytest.raises(cu.CashUpError, match="reason"):
        cu.add_movement(shop["tid"], DAY, "expense", 100, "", "manager")


def test_format_rand_is_exact_and_handles_negatives():
    assert cu.format_rand(123456) == "R 1,234.56"
    assert cu.format_rand(-5) == "-R 0.05"
    assert cu.format_rand(0) == "R 0.00"


def test_history_lists_closed_days_only(shop, clock):
    _sell(shop, "hist", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    assert cu.history(shop["tid"]) == []
    cu.close_day(shop["tid"], DAY, 10_000, "manager")
    assert [r["business_date"] for r in cu.history(shop["tid"])] == [DAY]


def test_cash_up_data_is_isolated_by_tenant(shop, clock):
    _sell(shop, "iso", "cash", "cash")
    cu.open_day(shop["tid"], DAY, 0, "manager")
    other, _ = _tenant("p3other")
    assert cu.cash_up_status(other, DAY) is None
    assert cu.history(other) == []
    assert cu.late_transactions(other) == []


# ── HTTP routes ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(clock):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login(client, username):
    import re as _re
    page = client.get("/login").get_data(as_text=True)
    token = _re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": token},
                           follow_redirects=True)
    meta = _re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    client.environ_base["HTTP_X_CSRFTOKEN"] = meta.group(1)


def _form_token(client, url="/cashup/"):
    page = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def test_anonymous_users_are_sent_to_login(client, shop):
    assert client.get("/cashup/").status_code == 302


def test_staff_without_permission_are_refused(client, shop):
    staff_tid, staff = _tenant("staffer")
    _login(client, staff)
    assert client.get("/cashup/").status_code == 403
    assert client.get("/cashup/history").status_code == 403


def test_no_setting_grants_staff_access_to_cash_up(client, shop):
    """The old staff override is gone: even a leftover setting row cannot open Cash-Up for a non-admin."""
    staff_tid, staff = _tenant("trusted")
    save_setting(staff_tid, "cashup_staff_can_manage", "1")
    _login(client, staff)
    assert client.get("/cashup/").status_code == 403
    assert client.post("/cashup/close", data={"counted_cash": "0", "confirm": "yes"}).status_code == 403


def test_admin_can_open_the_page(client, shop):
    admin_tid, admin = _tenant("boss", admin=True)
    _login(client, admin)
    response = client.get("/cashup/")
    assert response.status_code == 200
    assert "Daily Cash-Up" in response.get_data(as_text=True)


def test_posts_without_a_csrf_token_are_refused(client, shop):
    admin_tid, admin = _tenant("csrf", admin=True)
    _login(client, admin)
    client.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    assert client.post("/cashup/open", data={"opening_float": "10"}).status_code == 400
    assert cu.cash_up_status(admin_tid, shop_time.business_date_for(shop_time.utc_now())) is None


def test_full_day_through_the_routes(client, shop, clock):
    admin_tid, admin = _tenant("routes", admin=True)
    _sell_for(admin_tid, "route-sale", "cash")
    _login(client, admin)
    token = _form_token(client)
    client.post("/cashup/open", data={"csrf_token": token, "opening_float": "500.00"})
    assert cu.cash_up_status(admin_tid, DAY)["status"] == "open"
    client.post("/cashup/movement", data={"csrf_token": token, "kind": "expense", "amount": "12.50", "reason": "Tea"})
    assert cu.movements(admin_tid, DAY)[0]["amount_cents"] == 1250
    client.post("/cashup/movement", data={"csrf_token": token, "kind": "expense", "amount": "abc", "reason": "Bad"})
    assert len(cu.movements(admin_tid, DAY)) == 1, "invalid amounts are refused"
    bad_close = client.post("/cashup/close", data={"csrf_token": token, "counted_cash": "1.00"}, follow_redirects=True)
    assert cu.cash_up_status(admin_tid, DAY)["status"] == "open", "closing needs the confirmation"
    assert bad_close.status_code == 200
    # Expected: 500.00 float + 100.00 cash sale - 12.50 expense = 587.50. Counting exactly that closes the day.
    client.post("/cashup/close", data={"csrf_token": token, "counted_cash": "587.50", "confirm": "yes"})
    assert cu.cash_up_status(admin_tid, DAY)["status"] == "closed"
    client.post("/cashup/close", data={"csrf_token": token, "counted_cash": "587.50", "confirm": "yes"})
    assert cu.cash_up_status(admin_tid, DAY)["counted_cash_cents"] == 58_750, "a second close changes nothing"
    detail = client.get(f"/cashup/{DAY}")
    assert detail.status_code == 200 and "cannot be edited" in detail.get_data(as_text=True)
    assert client.get(f"/cashup/{DAY}/print").status_code == 200
    assert client.get("/cashup/history").status_code == 200


def test_tenant_isolation_on_the_pages(client, shop, clock):
    admin_tid, admin = _tenant("iso-admin", admin=True)
    cu.open_day(admin_tid, DAY, 0, "manager")
    other_tid, other = _tenant("iso-other", admin=True)
    _login(client, other)
    assert "Not opened" in client.get("/cashup/").get_data(as_text=True)


def _sell_for(tid, key, method):
    product = add_product(tid, f"ROUTE-{key}", "Route item", current_stock=10, selling_price=100)
    return complete_sale(tid, key, [{"type": "product", "id": product, "quantity": 1}], payment_method=method,
                         sale_date=DAY)
