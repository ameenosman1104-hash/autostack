"""Cash-Up in a real browser at desktop and phone widths: readable, fits the screen, controls work, print view works."""
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
from app.tenant_db import add_product, complete_sale, init_tenant_db

PASSWORD = "cashup-mobile-password"
MOMENT = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)      # 12:00 on 27 October in Johannesburg
TODAY, PREVIOUS = "2026-10-27", "2026-10-26"
VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "phone": {"width": 390, "height": 844}}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(shop_time, "utc_now", lambda: MOMENT)
    app = create_app()
    app.config.update(TESTING=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    username = "cashup_mobile_" + uuid4().hex[:8]
    tid, err = create_tenant("Mobile Cash Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    update_tenant(tid, is_admin=1)
    product = add_product(tid, "MOB-1", "Mobile item", current_stock=50, selling_price=100)
    complete_sale(tid, "mobile-prev", [{"type": "product", "id": product, "quantity": 1}], payment_method="cash",
                  sale_date=PREVIOUS)
    cu.open_day(tid, PREVIOUS, 0, "manager")
    cu.close_day(tid, PREVIOUS, 10_000, "manager")
    complete_sale(tid, "mobile-today", [{"type": "product", "id": product, "quantity": 1}], payment_method="card",
                  sale_date=TODAY)
    yield {"base": f"http://127.0.0.1:{server.server_port}", "tid": tid, "username": username}
    server.shutdown()


def _browser(p):
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


def _login(page, base, username):
    page.goto(base + "/login")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state()


def _no_horizontal_scroll(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("size", VIEWPORTS)
def test_dashboard_fits_the_screen_and_controls_are_usable(live, size):
    width = VIEWPORTS[size]["width"]
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport=VIEWPORTS[size])
        _login(page, live["base"], live["username"])
        page.goto(live["base"] + "/cashup/")
        page.fill('input[name="opening_float"]', "100.00")
        page.click("text=Open cash-up")
        page.wait_for_load_state()

        assert _no_horizontal_scroll(page), f"no sideways scrolling at {size} width"
        for selector in ("#countedCash", "#closeBtn", 'select[name="kind"]', 'input[name="amount"]'):
            box = page.locator(selector).first.bounding_box()
            assert box is not None and box["width"] > 0, selector
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, f"{selector} fits the {size} screen"

        page.fill("#countedCash", "1000.00")
        page.dispatch_event("#countedCash", "input")
        preview = page.inner_text("#variancePreview")
        assert "Surplus" in preview or "Shortage" in preview or "Balanced" in preview
        browser.close()


def test_closing_needs_the_confirmation_and_an_explanation_for_a_variance(live):
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport=VIEWPORTS["phone"])
        page.on("dialog", lambda d: d.accept())
        _login(page, live["base"], live["username"])
        page.goto(live["base"] + "/cashup/")
        page.fill('input[name="opening_float"]', "100.00")
        page.click("text=Open cash-up")
        page.wait_for_load_state()

        page.fill("#countedCash", "150.00")                     # expected is the 100.00 float (today's sale is card) → surplus
        page.click("#closeBtn")                                 # no confirmation ticked: the browser stops it
        page.wait_for_timeout(300)
        assert cu.cash_up_status(live["tid"], TODAY)["status"] == "open"

        page.check("#confirmClose")
        page.click("#closeBtn")                                 # ticked, but no explanation: the server refuses
        page.wait_for_load_state()
        page.wait_for_timeout(300)
        assert cu.cash_up_status(live["tid"], TODAY)["status"] == "open"
        assert "Explain the shortage" in page.text_content("body")

        # The refused attempt reloaded the page, so the count, explanation and confirmation must be entered again.
        page.fill("#countedCash", "150.00")
        page.fill("#varianceExplanation", "Extra float paid in by the owner")
        page.check("#confirmClose")
        page.click("#closeBtn")
        page.wait_for_load_state()
        page.wait_for_timeout(300)
        assert cu.cash_up_status(live["tid"], TODAY)["status"] == "closed"
        browser.close()


def test_admin_opens_a_historical_closed_cash_up_and_prints_it(live):
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        _login(page, live["base"], live["username"])
        page.goto(live["base"] + "/cashup/history")
        assert PREVIOUS in page.text_content("body")
        page.goto(live["base"] + f"/cashup/{PREVIOUS}")
        assert "Cash-up for 2026-10-26" in page.text_content("body")
        assert "cannot be edited" in page.text_content("body")
        page.goto(live["base"] + f"/cashup/{PREVIOUS}/print")
        page.emulate_media(media="print")
        assert page.is_visible("text=Expected closing cash")
        assert _no_horizontal_scroll(page)
        browser.close()
