"""Browser check of the debtor payment form: shop business date, one key per opening, double tap records once."""
import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.main_db import create_tenant
from app.services import shop_time
from app.tenant_db import add_debtor, get_conn, init_tenant_db

PASSWORD = "payment-form-browser"
# 22:30 UTC on 26 October: 00:30 on 27 October in Johannesburg, but 15:30 on 26 October in Los Angeles.
MOMENT = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(shop_time, "utc_now", lambda: MOMENT)
    app = create_app()
    app.config.update(TESTING=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    username = "payform_" + uuid4().hex[:10]
    tid, err = create_tenant("Payment Form Shop", username, "payform@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    debtor = add_debtor(tid, "Form Debtor", "", "form@example.com", 1000, "2026-09-09", "email", 28, "")
    yield {"base": f"http://127.0.0.1:{server.server_port}", "tid": tid, "username": username, "debtor": debtor}
    server.shutdown()


def _payments(tid, debtor):
    conn = get_conn(tid)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT amount_paid, business_date, recorded_at_utc, idempotency_key FROM payment_history "
            "WHERE debtor_id=?", (debtor,)).fetchall()]
    finally:
        conn.close()


def test_payment_form_uses_the_shop_date_and_a_double_tap_records_once(live):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        # A device set to Los Angeles: its own date is still the 26th when the shop's is the 27th.
        context = browser.new_context(timezone_id="America/Los_Angeles")
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())
        posted_keys = []
        page.on("request", lambda r: posted_keys.append(_key(r))
                if r.method == "POST" and r.url.endswith(f"/record-payment") else None)

        page.goto(live["base"] + "/login")
        page.fill('input[name="username"]', live["username"])
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state()
        page.goto(live["base"] + "/debtors/")
        page.evaluate(f"showPaymentQuickRecord({live['debtor']}, 'Form Debtor', 1000)")
        page.wait_for_selector("#quickPaymentForm:visible")

        assert page.input_value("#qpDate") == "2026-10-27", "the shop's business date, not the device's"
        page.fill("#qpAmount", "250")
        page.dblclick('#quickPaymentForm button[type="submit"]')
        page.wait_for_load_state()
        page.wait_for_timeout(800)
        browser.close()

    rows = _payments(live["tid"], live["debtor"])
    assert len(rows) == 1, f"a double tap must record one payment, found {len(rows)}"
    assert rows[0]["amount_paid"] == 250
    assert rows[0]["business_date"] == "2026-10-27"
    assert rows[0]["recorded_at_utc"] == "2026-10-26 22:30:00"
    assert rows[0]["idempotency_key"], "the form must send a key"
    keys = {k for k in posted_keys if k}
    assert len(keys) == 1, f"one opening of the form must send one key, saw {keys}"


def _key(request):
    data = request.post_data or ""
    marker = 'name="idempotency_key"'
    if marker not in data:
        return None
    return data.split(marker, 1)[1].split("\r\n\r\n", 1)[1].split("\r\n", 1)[0]
