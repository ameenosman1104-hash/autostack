"""Browser checks for the Job Card form and the POS services, against a live local server."""
import re
import threading
import time
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.main_db import create_tenant
from app.services import job_cards as jc
from app.services import technicians as tech
from app.tenant_db import add_customer, add_product, add_service, get_conn, init_tenant_db

PASSWORD = "browser-job-password"


@pytest.fixture
def live_app():
    app = create_app()
    app.config.update(TESTING=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def shop():
    username = "browser_" + uuid4().hex[:12]
    tid, err = create_tenant("Browser Garage", username, "browser@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    customer = add_customer(tid, "Thabo Mokoena", phone="0821234567")
    jc.upsert_vehicle(tid, customer, "CA123456", make="VW", model="Polo")
    service = add_service(tid, "Wheel Alignment", default_price=350)
    product = add_product(tid, "TYRE-1", "Widget Tyre", current_stock=10, selling_price=100)
    return {"tid": tid, "username": username, "customer": customer, "service": service, "product": product}


@pytest.fixture
def page(live_app, shop):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(live_app + "/login")
        page.fill('input[name="username"]', shop["username"])
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state()
        yield page
        browser.close()


def _stock(tid, product_id):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT current_stock FROM products WHERE id=?", (product_id,)).fetchone()[0]
    finally:
        conn.close()


def _count(tid, table):
    conn = get_conn(tid)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_warning_clears_once_a_customer_is_selected(page, live_app, shop):
    page.goto(live_app + "/jobs/new")
    page.click("#saveBtn")
    page.wait_for_selector("#formError:not(.d-none)")
    assert "select a customer" in page.inner_text("#formError").lower()
    page.fill("#customerSearch", "Thabo")
    page.wait_for_selector("#customerResults button")
    page.click("#customerResults button >> nth=0")
    page.wait_for_function("() => document.getElementById('formError').classList.contains('d-none')")
    assert page.input_value("#customerId") == str(shop["customer"])


def test_new_customer_option_creates_customer_and_job_with_technician(page, live_app, shop):
    tech_record = tech.create_technician(shop["tid"], "Sipho")
    page.goto(live_app + "/jobs/new")
    page.click('label[for="modeNew"]')
    page.wait_for_selector("#newPanel:not(.d-none)")
    assert page.is_hidden("#existingPanel")
    page.fill("#newCustomerName", "Lerato Dube")
    page.fill("#newCustomerPhone", "071 555 0199")
    page.fill("#newRegistration", "NB99871")
    page.fill("#newMake", "Toyota")
    page.select_option("#technicianSelect", str(tech_record["id"]))
    page.fill("#mileageIn", "")
    page.select_option("#serviceSelect", str(shop["service"]))
    page.click("#saveBtn")
    page.wait_for_url(re.compile(r".*/jobs/\d+$"))
    conn = get_conn(shop["tid"])
    try:
        row = conn.execute("SELECT j.technician_id, j.mileage_in, c.name FROM job_cards j "
                           "JOIN customers c ON c.id = j.customer_id").fetchone()
    finally:
        conn.close()
    assert row["name"] == "Lerato Dube"
    assert row["technician_id"] == tech_record["id"]
    assert row["mileage_in"] is None


def test_duplicate_phone_offers_the_existing_customer(page, live_app, shop):
    page.goto(live_app + "/jobs/new")
    page.click('label[for="modeNew"]')
    page.fill("#newCustomerName", "Copy Customer")
    page.fill("#newCustomerPhone", "082 123 4567")
    page.fill("#newRegistration", "CP00001")
    page.click("#saveBtn")
    page.wait_for_selector("#formError button")
    assert "already belongs" in page.inner_text("#formError")
    page.click("#formError button")
    page.wait_for_function("() => document.getElementById('formError').classList.contains('d-none')")
    assert page.input_value("#customerId") == str(shop["customer"])
    assert page.is_visible("#existingPanel")
    assert _count(shop["tid"], "customers") == 1


def test_duplicate_phone_is_reported_on_the_form(page, live_app, shop):
    page.goto(live_app + "/jobs/new")
    page.click('label[for="modeNew"]')
    page.fill("#newCustomerName", "Copy Customer")
    page.fill("#newCustomerPhone", "+27 82 123 4567")
    page.fill("#newRegistration", "DU00001")
    customers_before = _count(shop["tid"], "customers")
    page.click("#saveBtn")
    page.wait_for_selector("#formError:not(.d-none)")
    assert "already belongs" in page.inner_text("#formError")
    assert _count(shop["tid"], "customers") == customers_before


def test_pos_service_sale_leaves_stock_alone_and_quantity_controls_work(page, live_app, shop):
    page.goto(live_app + "/pos/")
    page.wait_for_selector("#servicesGrid .product-card")
    page.click("#servicesGrid .product-card >> nth=0")
    page.fill("#productSearch", "Widget")
    page.wait_for_selector("#productsGrid .product-card")
    page.click("#productsGrid .product-card >> nth=0")
    page.locator("#basketItems .basket-item", has_text="Widget Tyre").locator("button", has_text="+").click()
    basket_text = page.inner_text("#basketItems")
    assert "Wheel Alignment" in basket_text and "Widget Tyre" in basket_text
    stock_before = _stock(shop["tid"], shop["product"])
    page.click("#completeBtn")
    page.wait_for_function("() => document.body.innerText.includes('Sale completed')")
    deadline = time.time() + 15
    while _count(shop["tid"], "invoices") < 1 and time.time() < deadline:
        time.sleep(0.2)
    assert _stock(shop["tid"], shop["product"]) == stock_before - 2, "the product's stock changes by the sold quantity"
    conn = get_conn(shop["tid"])
    try:
        lines = conn.execute("SELECT item_type, quantity, unit_price FROM invoice_items ORDER BY id").fetchall()
    finally:
        conn.close()
    kinds = sorted((r["item_type"], r["quantity"], r["unit_price"]) for r in lines)
    assert ("product", 2, 100) in kinds and ("service", 1, 350) in kinds
