"""Behavioural POS checks in a real browser: the Complete Sale button submits the sale, once per tap.

These replace the old static checks that searched the template source for particular handler text.
"""
import threading
import time
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import add_product, get_conn, init_tenant_db

PASSWORD = "pos-browser-password"


@pytest.fixture
def live_app():
    """Run the app on a local port so the browser talks to the same databases as the test."""
    app = create_app()
    app.config.update(TESTING=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def shop(live_app):
    """A tenant with one product in stock. Returns (tid, username, product_id)."""
    username = "pos_" + uuid4().hex[:12]
    tid, err = create_tenant("POS Shop", username, "pos@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    product_id = add_product(tid, "TYRE-1", "Widget Tyre", current_stock=10, selling_price=100)
    return tid, username, product_id


@pytest.fixture
def browser_page(live_app, shop):
    _, base = live_app
    _, username, _ = shop
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/login")
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state()
        page.goto(base + "/pos/")
        yield page
        browser.close()


def _invoice_count(tid):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    finally:
        conn.close()


def _stock(tid, product_id):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT current_stock FROM products WHERE id=?", (product_id,)).fetchone()[0]
    finally:
        conn.close()


def _wait_for_invoice_count(tid, expected, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _invoice_count(tid) == expected:
            return
        time.sleep(0.2)
    raise AssertionError(f"expected {expected} invoices, found {_invoice_count(tid)}")


def _add_widget_to_basket(page):
    page.fill("#productSearch", "Widget")
    page.wait_for_selector(".product-card")
    page.click(".product-card >> nth=0")


def test_complete_sale_button_submits_the_sale(browser_page, shop):
    tid, _, product_id = shop
    _add_widget_to_basket(browser_page)
    browser_page.click("#completeBtn")
    browser_page.wait_for_selector("text=Sale completed!")
    _wait_for_invoice_count(tid, 1)
    assert _stock(tid, product_id) == 9


def test_second_sale_on_the_same_page_is_recorded(browser_page, shop):
    tid, _, product_id = shop
    _add_widget_to_basket(browser_page)
    browser_page.click("#completeBtn")
    _wait_for_invoice_count(tid, 1)

    _add_widget_to_basket(browser_page)
    browser_page.click("#completeBtn")
    _wait_for_invoice_count(tid, 2)

    conn = get_conn(tid)
    try:
        numbers = [r[0] for r in conn.execute("SELECT invoice_number FROM invoices").fetchall()]
    finally:
        conn.close()
    assert len(set(numbers)) == 2, "each sale must get its own invoice number"
    assert _stock(tid, product_id) == 8


def test_double_tap_sends_one_sale(browser_page, shop):
    tid, _, _ = shop
    _add_widget_to_basket(browser_page)

    sale_requests = []
    browser_page.on("request", lambda r: sale_requests.append(r)
                    if r.method == "POST" and r.url.endswith("/pos/complete") else None)
    browser_page.dblclick("#completeBtn")
    browser_page.wait_for_selector("text=Sale completed!")
    _wait_for_invoice_count(tid, 1)
    time.sleep(0.5)  # allow any stray second request to arrive before counting
    assert len(sale_requests) == 1
    assert _invoice_count(tid) == 1
