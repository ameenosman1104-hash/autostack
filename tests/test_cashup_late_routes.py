"""The late-transaction inclusion route, its permissions and CSRF, and proof that Cash-Up never alters sales,
stock or debtor balances."""
import re
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant, update_tenant
from app.services import cashup as cu
from app.services import shop_time
from app.tenant_db import add_debtor, add_payment, add_product, complete_sale, get_conn, get_debtor, init_tenant_db

PASSWORD = "late-route-password"
PREVIOUS = "2026-10-26"
TODAY = "2026-10-27"


@pytest.fixture
def clock(monkeypatch):
    # 22:30 UTC on 26 October: it is already 27 October in Johannesburg, so "today" is 27 October.
    monkeypatch.setattr(shop_time, "utc_now", lambda: datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc))


def _tenant(name, admin):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    if admin:
        update_tenant(tid, is_admin=1)
    return tid, username


@pytest.fixture
def shop(clock):
    tid, admin = _tenant("late-admin", admin=True)
    return {"tid": tid, "admin": admin,
            "product": add_product(tid, "LATE-1", "Late item", current_stock=50, selling_price=100),
            "debtor": add_debtor(tid, "Holder", "", "h@example.com", 1000, PREVIOUS, "email", 28, "")}


@pytest.fixture
def client(clock):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login(client, username):
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": token},
                           follow_redirects=True)
    meta = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    client.environ_base["HTTP_X_CSRFTOKEN"] = meta.group(1)


def _token(client):
    """The session's CSRF token, from the page meta tag, which every page carries even when it has no forms."""
    page = client.get("/cashup/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def _sale(shop, key, method, sale_date):
    return complete_sale(shop["tid"], key, [{"type": "product", "id": shop["product"], "quantity": 1}],
                         payment_method=method, sale_date=sale_date)


def _rows(tid, sql, params=()):
    conn = get_conn(tid)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _close_previous_day(shop):
    """A closed earlier day, so a sale dated to it is a late transaction once it arrives."""
    _sale(shop, "previous-day", "cash", PREVIOUS)
    cu.open_day(shop["tid"], PREVIOUS, 0, "manager")
    cu.close_day(shop["tid"], PREVIOUS, 10_000, "manager")


def test_admin_includes_an_eligible_late_transaction_once(client, shop):
    _close_previous_day(shop)
    late = _sale(shop, "late-cash", "cash", PREVIOUS)
    _login(client, shop["admin"])
    client.post("/cashup/open", data={"csrf_token": _token(client), "opening_float": "0"})
    token = _token(client)
    response = client.post("/cashup/late/include", data={"csrf_token": token, "source_type": "invoice",
                                                         "source_id": str(late["invoice_id"])},
                           follow_redirects=True)
    assert "Original business date 2026-10-26 kept" in response.get_data(as_text=True)

    invoice = _rows(shop["tid"], "SELECT business_date, recorded_at_utc FROM invoices WHERE id=?", (late["invoice_id"],))[0]
    assert invoice["business_date"] == PREVIOUS, "the original business date never changes"
    lines = _rows(shop["tid"], "SELECT business_date, is_late FROM cash_up_lines WHERE source_type='invoice' AND source_id=?",
                  (late["invoice_id"],))
    assert lines == [{"business_date": PREVIOUS, "is_late": 1}]
    summary = cu.day_summary(shop["tid"], TODAY)
    assert summary["cash_sales_cents"] == 10_000 and summary["late_transactions"] == 1


def test_a_second_inclusion_is_refused_without_double_counting(client, shop):
    _close_previous_day(shop)
    late = _sale(shop, "twice-route", "cash", PREVIOUS)
    _login(client, shop["admin"])
    client.post("/cashup/open", data={"csrf_token": _token(client), "opening_float": "0"})
    data = {"csrf_token": _token(client), "source_type": "invoice", "source_id": str(late["invoice_id"])}
    client.post("/cashup/late/include", data=data)
    response = client.post("/cashup/late/include", data=data, follow_redirects=True)
    assert "already counted" in response.get_data(as_text=True)
    assert len(_rows(shop["tid"], "SELECT id FROM cash_up_lines WHERE source_id=? AND source_type='invoice'",
                     (late["invoice_id"],))) == 1
    assert cu.day_summary(shop["tid"], TODAY)["cash_sales_cents"] == 10_000


def test_a_closed_target_cash_up_cannot_be_changed_by_inclusion(client, shop):
    _close_previous_day(shop)
    _sale(shop, "already-closed-target", "cash", TODAY)     # the target day's own sale
    cu.open_day(shop["tid"], TODAY, 0, "manager")
    cu.close_day(shop["tid"], TODAY, 10_000, "manager")
    waiting = _sale(shop, "waiting-late", "cash", PREVIOUS)
    before = cu.day_summary(shop["tid"], TODAY)
    _login(client, shop["admin"])
    response = client.post("/cashup/late/include", data={"csrf_token": _token(client), "source_type": "invoice",
                                                         "source_id": str(waiting["invoice_id"])}, follow_redirects=True)
    assert "Open the cash-up" in response.get_data(as_text=True)
    assert _rows(shop["tid"], "SELECT id FROM cash_up_lines WHERE source_id=? AND source_type='invoice'",
                 (waiting["invoice_id"],)) == []
    assert cu.day_summary(shop["tid"], TODAY) == before


def test_non_admin_staff_receive_403(client, shop):
    _close_previous_day(shop)
    late = _sale(shop, "staff-try", "cash", PREVIOUS)
    staff_tid, staff = _tenant("late-staff", admin=False)
    _login(client, staff)
    assert client.post("/cashup/late/include", data={"source_type": "invoice", "source_id": str(late["invoice_id"])}
                       ).status_code == 403
    assert _rows(shop["tid"], "SELECT id FROM cash_up_lines WHERE source_id=?", (late["invoice_id"],)) == []


def test_csrf_protects_the_inclusion_route(client, shop):
    _close_previous_day(shop)
    late = _sale(shop, "csrf-late", "cash", PREVIOUS)
    _login(client, shop["admin"])
    client.post("/cashup/open", data={"csrf_token": _token(client), "opening_float": "0"})
    client.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    response = client.post("/cashup/late/include", data={"source_type": "invoice", "source_id": str(late["invoice_id"])})
    assert response.status_code == 400
    assert _rows(shop["tid"], "SELECT id FROM cash_up_lines WHERE source_id=?", (late["invoice_id"],)) == []


def test_a_payment_cannot_be_backdated_through_the_form(client, shop):
    """A receipt is recorded on today's business date only; a backdated date is refused and nothing is written."""
    _login(client, shop["admin"])
    token = _token(client)
    client.environ_base["HTTP_X_CSRFTOKEN"] = token
    response = client.post(f"/debtors/{shop['debtor']}/record-payment",
                           data={"csrf_token": token, "amount": "100", "payment_method": "cash",
                                 "payment_date": PREVIOUS}, follow_redirects=True)
    assert "business date (" in response.get_data(as_text=True)
    assert _rows(shop["tid"], "SELECT id FROM payment_history") == []
    response = client.post(f"/debtors/{shop['debtor']}/record-payment",
                           data={"csrf_token": token, "amount": "100", "payment_method": "cash",
                                 "payment_date": TODAY})
    assert response.status_code == 302
    assert _rows(shop["tid"], "SELECT business_date FROM payment_history")[0]["business_date"] == TODAY


def test_cash_up_operations_do_not_alter_sales_stock_or_debtor_balances(client, shop):
    _close_previous_day(shop)
    late = _sale(shop, "untouched-late", "cash", PREVIOUS)
    add_payment(shop["tid"], shop["debtor"], 250, payment_date=PREVIOUS, payment_method="cash")
    payment = _rows(shop["tid"], "SELECT id FROM payment_history ORDER BY id DESC LIMIT 1")[0]["id"]

    def snapshot():
        return (_rows(shop["tid"], "SELECT id, total, payment_method, payment_status, business_date FROM invoices ORDER BY id"),
                _rows(shop["tid"], "SELECT id, current_stock FROM products ORDER BY id"),
                _rows(shop["tid"], "SELECT id, amount_paid, business_date FROM payment_history ORDER BY id"),
                get_debtor(shop["tid"], shop["debtor"])["amount_owed"], get_debtor(shop["tid"], shop["debtor"])["total_paid_to_date"])

    before = snapshot()
    _login(client, shop["admin"])
    client.post("/cashup/open", data={"csrf_token": _token(client), "opening_float": "10"})
    client.post("/cashup/movement", data={"csrf_token": _token(client), "kind": "expense", "amount": "5", "reason": "Tea"})
    client.post("/cashup/late/include", data={"csrf_token": _token(client), "source_type": "invoice",
                                              "source_id": str(late["invoice_id"])})
    client.post("/cashup/late/include", data={"csrf_token": _token(client), "source_type": "payment",
                                              "source_id": str(payment)})
    client.post("/cashup/close", data={"csrf_token": _token(client), "counted_cash": "0", "confirm": "yes"})
    assert snapshot() == before, "Cash-Up never changes invoices, stock, payments or debtor balances"
