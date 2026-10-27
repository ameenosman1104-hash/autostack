"""Browser checks for the Daily Cash-Up page: open, movement, count preview, explained close, double tap, history."""
import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.main_db import create_tenant, update_tenant
from app.services import cashup as cu
from app.services import shop_time
from app.tenant_db import add_product, complete_sale, get_conn, init_tenant_db, save_setting

PASSWORD = "cashup-browser-password"
MOMENT = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)   # 27 October in Johannesburg
DAY = "2026-10-27"


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(shop_time, "utc_now", lambda: MOMENT)
    app = create_app()
    app.config.update(TESTING=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tenants = {}
    for role in ("admin", "staff"):
        username = f"cashup_{role}_{uuid4().hex[:8]}"
        tid, err = create_tenant(f"Cash {role}", username, f"{username}@example.com", PASSWORD)
        assert not err
        init_tenant_db(tid)
        if role == "admin":
            update_tenant(tid, is_admin=1)
        tenants[role] = {"tid": tid, "username": username}
    product = add_product(tenants["admin"]["tid"], "BR-CASH", "Browser item", current_stock=20, selling_price=100)
    complete_sale(tenants["admin"]["tid"], "browser-sale", [{"type": "product", "id": product, "quantity": 1}],
                  payment_method="cash")
    yield {"base": f"http://127.0.0.1:{server.server_port}", **tenants}
    server.shutdown()


def _login(page, base, username):
    page.goto(base + "/login")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state()


def _browser(p):
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


def test_admin_opens_counts_and_closes_the_day_once(live):
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())
        _login(page, live["base"], live["admin"]["username"])
        page.goto(live["base"] + "/cashup/")
        assert "Not opened" in page.text_content("body")

        page.fill('input[name="opening_float"]', "500.00")
        page.click("text=Open cash-up")
        page.wait_for_load_state()
        assert "Open" in page.text_content("body")

        page.fill('input[name="amount"]', "12.50")
        page.fill('input[name="reason"]', "Tea for the team")
        page.select_option('select[name="kind"]', "expense")
        page.click("text=Record movement")
        page.wait_for_load_state()
        assert "Tea for the team" in page.text_content("body")

        page.fill("#countedCash", "580.00")
        page.dispatch_event("#countedCash", "input")
        assert "Shortage: R 7.50" in page.inner_text("#variancePreview")

        page.fill("#varianceExplanation", "Change given short on a cash sale")
        page.check("#confirmClose")
        page.dblclick("#closeBtn")
        page.wait_for_load_state()
        page.wait_for_timeout(500)
        assert "Closed" in page.text_content("body")
        browser.close()

    summary = cu.cash_up_status(live["admin"]["tid"], DAY)
    assert summary["status"] == "closed"
    assert summary["counted_cash_cents"] == 58_000
    assert summary["variance_cents"] == -750
    rows = [r for r in cu.history(live["admin"]["tid"]) if r["business_date"] == DAY]
    assert len(rows) == 1, "a double tap must close the day once"


def test_staff_without_permission_see_no_cash_up_link(live):
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        _login(page, live["base"], live["staff"]["username"])
        page.goto(live["base"] + "/debtors/")
        assert page.locator("text=Cash-Up").count() == 0
        response = page.goto(live["base"] + "/cashup/")
        assert response.status == 403
        browser.close()


def test_a_leftover_setting_does_not_show_the_link_to_staff(live):
    save_setting(live["staff"]["tid"], "cashup_staff_can_manage", "1")
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        _login(page, live["base"], live["staff"]["username"])
        page.goto(live["base"] + "/debtors/")
        assert page.locator("text=Cash-Up").count() == 0
        assert page.goto(live["base"] + "/cashup/").status == 403
        browser.close()
