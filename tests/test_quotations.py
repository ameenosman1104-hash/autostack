"""Quotation regression tests: numbering, pricing rules, workflow, PDF, print and WhatsApp share."""
import io
import re
import os
from urllib.parse import unquote

import pytest
from pypdf import PdfReader

import sys

from app.services import quotations as engine
from app.services import quotation_output as output
from app.tenant_db import get_conn, save_setting
from app.db_migrations import LATEST_SCHEMA_VERSION

TID = 10


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Fresh app package per test: other test modules delete app.* from sys.modules, leaving stale main DB paths."""
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSTACK_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        del sys.modules[name]
    try:
        import app.main_db as main_db
        monkeypatch.setattr(main_db, "MAIN_DB", str(tmp_path / "main.db"))
        from app import create_app
        monkeypatch.setattr("app.services.auto_sync.start_background_sync", lambda app: None)
        application = create_app()
        application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        yield application
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("""INSERT INTO tenants (id, business_name, username, password_hash)
                    VALUES (10, 'Test Shop', 'testuser', 'not-a-real-password')""")
    conn.commit()
    conn.close()
    with client.session_transaction() as session:
        session["_user_id"] = "10"
        session["_fresh"] = True
    return {"X-Tenant-ID": "10"}


def custom(price=100, qty=1, disc=0, description="Wheel balancing"):
    return {"item_type": "custom", "description": description, "quantity": qty, "unit_price": price, "discount_pct": disc}


def quote(items=None, **extra):
    data = {"customer_name": "Thabo Mokoena", "customer_phone": "082 123 4567",
            "quote_date": "2026-10-18", "expiry_date": "2026-11-01", "items": items or [custom()]}
    data.update(extra)
    return data


def product(price=100.0, code="T-205"):
    conn = get_conn(TID)
    cur = conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                       "VALUES (?, 'Touring tyre', 'Brand', '205/55R16', 'Touring', ?, 10, 0)", (code, price))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def backups_dir():
    return os.path.join(os.environ["AUTOSTACK_DATA_DIR"], "tenants", ".backups")


def test_migration_creates_quotation_tables(app, auth_headers):
    conn = get_conn(TID)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"quotations", "quotation_items", "quotation_events", "quotation_sequence"} <= tables
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == LATEST_SCHEMA_VERSION == 31
    conn.close()


def test_numbers_are_sequential_and_unique(app, auth_headers):
    first = engine.create_quotation(TID, quote(), "tester", False)
    second = engine.create_quotation(TID, quote(), "tester", False)
    assert first["quotation_number"] == "QUO-000001"
    assert second["quotation_number"] == "QUO-000002"


def test_totals_apply_discount_and_round(app, auth_headers):
    result = engine.create_quotation(TID, quote([custom(price=100, qty=2, disc=10)]), "tester", False)
    detail = engine.get_quotation(TID, result["id"])
    assert detail["items"][0]["line_total"] == 180.0
    assert detail["header"]["subtotal"] == 200.0
    assert detail["header"]["discount_total"] == 20.0
    assert detail["header"]["total"] == 180.0


def test_staff_cannot_change_product_price_admin_can(app, auth_headers):
    pid = product(price=100.0)
    item = {"item_type": "product", "product_id": pid, "quantity": 1, "unit_price": 90, "discount_pct": 0}
    with pytest.raises(engine.QuotationError, match="not authorised"):
        engine.create_quotation(TID, quote([item]), "staff", False)
    result = engine.create_quotation(TID, quote([item]), "owner", True)
    assert engine.get_quotation(TID, result["id"])["items"][0]["unit_price"] == 90.0


def test_staff_discount_cap_and_admin_override(app, auth_headers):
    with pytest.raises(engine.QuotationError, match="exceeds your limit"):
        engine.create_quotation(TID, quote([custom(disc=15)]), "staff", False)
    engine.create_quotation(TID, quote([custom(disc=15)]), "owner", True)
    save_setting(TID, "quote_max_discount_pct", "20")
    engine.create_quotation(TID, quote([custom(disc=15)]), "staff", False)


def test_expiry_cannot_precede_quote_date(app, auth_headers):
    with pytest.raises(engine.QuotationError, match="cannot be before"):
        engine.create_quotation(TID, quote(quote_date="2026-10-18", expiry_date="2026-10-01"), "tester", False)


def test_only_drafts_are_editable_and_transitions_enforced(app, auth_headers):
    created = engine.create_quotation(TID, quote(), "tester", False)
    qid = created["id"]
    with pytest.raises(engine.QuotationError, match="Cannot change status"):
        engine.set_status(TID, qid, "accepted", "tester")
    engine.set_status(TID, qid, "sent", "tester")
    with pytest.raises(engine.QuotationError, match="Only draft"):
        engine.update_quotation(TID, qid, quote([custom(price=50)]), "tester", False)
    engine.set_status(TID, qid, "accepted", "tester")
    with pytest.raises(engine.QuotationError, match="Cannot change status"):
        engine.set_status(TID, qid, "sent", "tester")


def test_overdue_quotes_expire_with_audit_event(app, auth_headers):
    created = engine.create_quotation(TID, quote(quote_date="2026-01-01", expiry_date="2026-01-10"), "tester", False)
    rows = engine.list_quotations(TID)
    match = [r for r in rows if r["id"] == created["id"]][0]
    assert match["status"] == "expired"
    events = engine.get_quotation(TID, created["id"])["events"]
    assert any(e["event"] == "expired" for e in events)


def test_normal_operations_create_no_backups(app, auth_headers):
    engine.create_quotation(TID, quote(), "tester", False)
    engine.list_quotations(TID)
    engine.search_customers(TID, "Th")
    assert not os.path.isdir(backups_dir()) or os.listdir(backups_dir()) == []


def test_pdf_is_valid_and_spans_multiple_pages(app, auth_headers):
    items = [custom(description=f"Tyre service line {n}", price=50 + n) for n in range(70)]
    created = engine.create_quotation(TID, quote(items), "tester", False)
    detail = engine.get_quotation(TID, created["id"])
    shop = {"name": "Test Shop", "phone": "", "email": "", "address": "", "logo_path": "", "currency": "R", "country_code": "27"}
    content = output.render_pdf(detail["header"], detail["items"], shop)
    assert content.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(content)).pages) >= 2


def test_pdf_escapes_special_characters(app, auth_headers):
    header = {"quotation_number": "QUO-000099", "customer_name": "A&B <Tyres> Ltd", "customer_phone": "", "customer_email": "",
              "quote_date": "2026-10-18", "expiry_date": "2026-11-01", "subtotal": 10, "discount_total": 0, "total": 10,
              "notes": "Use <b> & keep", "terms": "Pay on delivery"}
    items = [{"position": 1, "description": "Tyre & rim <XL>", "detail": "a<b", "quantity": 1,
              "unit_price": 10, "discount_pct": 0, "line_total": 10}]
    shop = {"name": "Shop & Co", "phone": "", "email": "", "address": "", "logo_path": "../../etc/passwd", "currency": "R", "country_code": ""}
    content = output.render_pdf(header, items, shop)
    assert content.startswith(b"%PDF")


def test_whatsapp_link_normalises_number_and_includes_details(app, auth_headers):
    quotation = {"quotation_number": "QUO-000007", "customer_name": "Thabo", "customer_phone": "082 123 4567",
                 "total": 1234.5, "expiry_date": "2026-11-01"}
    shop = {"name": "Tyre King", "currency": "R", "country_code": "27"}
    url = output.whatsapp_url(quotation, shop)
    assert url.startswith("https://wa.me/27821234567?text=")
    text = unquote(url.split("?text=", 1)[1])
    assert "QUO-000007" in text and "R1,234.50" in text and "2026-11-01" in text and "Tyre King" in text


def test_whatsapp_requires_phone_and_country_code(app, auth_headers):
    shop = {"name": "Shop", "currency": "R", "country_code": ""}
    with pytest.raises(output.ShareError, match="phone number"):
        output.whatsapp_url({"quotation_number": "Q", "customer_name": "X", "customer_phone": "", "total": 1, "expiry_date": "2026-11-01"}, shop)
    with pytest.raises(output.ShareError, match="country code"):
        output.whatsapp_url({"quotation_number": "Q", "customer_name": "X", "customer_phone": "0821234567", "total": 1, "expiry_date": "2026-11-01"}, shop)


def test_logo_path_must_stay_inside_static(app, auth_headers):
    assert output.logo_file("../../main.py") is None
    assert output.logo_file("") is None


def test_print_page_has_no_navigation_and_prints_all_items(app, client, auth_headers):
    created = engine.create_quotation(TID, quote([custom(description="Alignment")]), "tester", False)
    html = client.get(f"/quotations/{created['id']}/print").get_data(as_text=True)
    assert "@media print" in html and "QUO-000001" in html and "Alignment" in html
    assert "navbar" not in html and "Logout" not in html and "Quotations</a>" not in html


def test_quotation_routes_require_login(client):
    response = client.get("/quotations/")
    assert response.status_code in (301, 302) and "login" in response.headers.get("Location", "")


def test_json_save_and_view_flow(app, client, auth_headers):
    response = client.post("/quotations/save", json=quote(), headers=auth_headers)
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["quotation_number"] == "QUO-000001"
    page = client.get(body["url"]).get_data(as_text=True)
    assert "Share via WhatsApp" in page and "Download PDF" in page and "Print" in page
    pdf = client.get(f"/quotations/{body['id']}/pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert pdf.headers["Content-Disposition"].startswith("attachment")


def stock_of(pid):
    conn = get_conn(TID)
    value = conn.execute("SELECT current_stock FROM products WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    return value


def invoice_count():
    conn = get_conn(TID)
    value = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    conn.close()
    return value


def accepted_quote(items, days=30):
    from datetime import date, timedelta
    today = date.today()
    created = engine.create_quotation(TID, quote(items, quote_date=today.isoformat(),
                                                 expiry_date=(today + timedelta(days=days)).isoformat()), "tester", True)
    engine.set_status(TID, created["id"], "sent", "tester")
    engine.set_status(TID, created["id"], "accepted", "tester")
    return created["id"]


def product_line(pid, qty=2, price=100.0, disc=0):
    return {"item_type": "product", "product_id": pid, "quantity": qty, "unit_price": price, "discount_pct": disc}


def test_conversion_deducts_stock_once_and_links_invoice(app, auth_headers):
    pid = product(price=100.0, code="CONV-1")
    qid = accepted_quote([product_line(pid, qty=2, price=100.0, disc=10)])
    before = stock_of(pid)
    result = engine.convert_to_sale(TID, qid, "cashier", "cash")
    assert result["duplicate"] is False and result["invoice_number"]
    assert stock_of(pid) == before - 2
    detail = engine.get_quotation(TID, qid)
    assert detail["header"]["status"] == "converted"
    assert detail["header"]["converted_invoice_id"] == result["invoice_id"]
    assert detail["items"][0]["unit_price"] == 100.0 and detail["items"][0]["discount_pct"] == 10
    conn = get_conn(TID)
    assert conn.execute("SELECT total FROM invoices WHERE id=?", (result["invoice_id"],)).fetchone()[0] == 180.0
    conn.close()


def test_repeat_conversion_returns_original_invoice_without_second_sale(app, auth_headers):
    pid = product(price=50.0, code="CONV-2")
    qid = accepted_quote([product_line(pid, qty=1, price=50.0)])
    first = engine.convert_to_sale(TID, qid, "cashier")
    stock_after = stock_of(pid)
    invoices_after = invoice_count()
    second = engine.convert_to_sale(TID, qid, "cashier")
    assert second["duplicate"] is True and second["invoice_id"] == first["invoice_id"]
    assert stock_of(pid) == stock_after and invoice_count() == invoices_after


def test_failed_link_rolls_back_sale_and_stock(app, auth_headers, monkeypatch):
    pid = product(price=40.0, code="CONV-3")
    qid = accepted_quote([product_line(pid, qty=3, price=40.0)])
    before, invoices = stock_of(pid), invoice_count()

    def boom(conn, quotation_id, invoice_id, actor, number):
        raise RuntimeError("injected link failure")
    monkeypatch.setattr(engine, "_mark_converted", boom)
    with pytest.raises(Exception, match="injected link failure"):
        engine.convert_to_sale(TID, qid, "cashier")
    assert stock_of(pid) == before
    assert invoice_count() == invoices
    assert engine.get_quotation(TID, qid)["header"]["status"] == "accepted"


def test_insufficient_stock_blocks_conversion(app, auth_headers):
    pid = product(price=30.0, code="CONV-4")
    qid = accepted_quote([product_line(pid, qty=50, price=30.0)])
    before, invoices = stock_of(pid), invoice_count()
    with pytest.raises(engine.QuotationError, match="Insufficient stock"):
        engine.convert_to_sale(TID, qid, "cashier")
    assert stock_of(pid) == before and invoice_count() == invoices
    assert engine.get_quotation(TID, qid)["header"]["status"] == "accepted"


def test_changed_price_blocks_conversion_and_keeps_quote_price(app, auth_headers):
    pid = product(price=60.0, code="CONV-5")
    qid = accepted_quote([product_line(pid, qty=1, price=60.0)])
    conn = get_conn(TID)
    conn.execute("UPDATE products SET selling_price=75 WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    invoices = invoice_count()
    with pytest.raises(engine.QuotationError, match="Prices have changed"):
        engine.convert_to_sale(TID, qid, "cashier")
    assert invoice_count() == invoices
    assert engine.get_quotation(TID, qid)["items"][0]["unit_price"] == 60.0


@pytest.mark.parametrize("state", ["draft", "sent", "rejected", "expired"])
def test_only_accepted_quotes_convert(app, auth_headers, state):
    pid = product(price=20.0, code=f"CONV-S-{state}")
    created = engine.create_quotation(TID, quote([product_line(pid, qty=1, price=20.0)]), "tester", True)
    qid = created["id"]
    if state in ("sent", "rejected"):
        engine.set_status(TID, qid, "sent", "tester")
        if state == "rejected":
            engine.set_status(TID, qid, "rejected", "tester")
    if state == "expired":
        engine.set_status(TID, qid, "sent", "tester")
        engine.set_status(TID, qid, "accepted", "tester")
        conn = get_conn(TID)
        conn.execute("UPDATE quotations SET expiry_date='2000-01-01' WHERE id=?", (qid,))
        conn.commit()
        conn.close()
    before = stock_of(pid)
    with pytest.raises(engine.QuotationError):
        engine.convert_to_sale(TID, qid, "cashier")
    assert stock_of(pid) == before


def test_custom_lines_cannot_convert(app, auth_headers):
    qid = accepted_quote([custom(price=50)])
    with pytest.raises(engine.QuotationError, match="Custom lines"):
        engine.convert_to_sale(TID, qid, "cashier")


def test_conversion_is_tenant_scoped(app, auth_headers):
    pid = product(price=10.0, code="CONV-T")
    qid = accepted_quote([product_line(pid, qty=1, price=10.0)])
    with pytest.raises(engine.QuotationError, match="not found"):
        engine.convert_to_sale(99, qid, "cashier")


def test_convert_route_requires_login_and_returns_invoice(app, client, auth_headers):
    pid = product(price=25.0, code="CONV-R")
    qid = accepted_quote([product_line(pid, qty=1, price=25.0)])
    anonymous = app.test_client().post(f"/quotations/{qid}/convert", json={})
    assert anonymous.status_code in (301, 302, 401)
    save_setting(TID, "quote_staff_can_convert", "1")
    response = client.post(f"/quotations/{qid}/convert", json={"payment_method": "card"}, headers=auth_headers)
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["invoice_number"] and body["duplicate"] is False
    again = client.post(f"/quotations/{qid}/convert", json={}, headers=auth_headers)
    assert again.status_code == 200 and again.get_json()["duplicate"] is True


def set_admin(flag):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("UPDATE tenants SET is_admin=? WHERE id=?", (1 if flag else 0, TID))
    conn.commit()
    conn.close()


def test_staff_cannot_convert_by_default(app, client, auth_headers):
    pid = product(price=15.0, code="PERM-1")
    qid = accepted_quote([product_line(pid, qty=1, price=15.0)])
    stock = stock_of(pid)
    response = client.post(f"/quotations/{qid}/convert", json={}, headers=auth_headers)
    assert response.status_code == 403
    assert stock_of(pid) == stock
    assert engine.get_quotation(TID, qid)["header"]["status"] == "accepted"


def test_admin_can_convert_without_setting(app, client, auth_headers):
    pid = product(price=15.0, code="PERM-2")
    qid = accepted_quote([product_line(pid, qty=1, price=15.0)])
    set_admin(True)
    response = client.post(f"/quotations/{qid}/convert", json={}, headers=auth_headers)
    assert response.status_code == 201, response.get_json()


def test_staff_convert_requires_admin_enablement_and_view_hides_button(app, client, auth_headers):
    pid = product(price=15.0, code="PERM-3")
    qid = accepted_quote([product_line(pid, qty=1, price=15.0)])
    assert "Convert to Sale" not in client.get(f"/quotations/{qid}").get_data(as_text=True)
    save_setting(TID, "quote_staff_can_convert", "1")
    assert "Convert to Sale" in client.get(f"/quotations/{qid}").get_data(as_text=True)
    response = client.post(f"/quotations/{qid}/convert", json={}, headers=auth_headers)
    assert response.status_code == 201, response.get_json()


def test_whatsapp_country_code_defaults_to_27_and_is_configurable(app, auth_headers):
    shop = output.shop_profile(TID)
    assert shop["country_code"] == "27"
    quotation = {"quotation_number": "QUO-000003", "customer_name": "Sipho", "customer_phone": "0821234567",
                 "total": 100, "expiry_date": "2026-11-01"}
    assert output.whatsapp_url(quotation, shop).startswith("https://wa.me/27821234567?")
    save_setting(TID, "whatsapp_country_code", "264")
    assert output.whatsapp_url(quotation, output.shop_profile(TID)).startswith("https://wa.me/264821234567?")


def test_sidebar_quotations_link_has_file_text_icon(app, client, auth_headers):
    html = client.get("/quotations/").get_data(as_text=True)
    assert re.search(r'<i class="bi bi-file-text me-2"></i>\s*Quotations', html)


def test_staff_cannot_change_settings(app, client, auth_headers):
    response = client.post("/quotations/settings", json={"currency_symbol": "$"}, headers=auth_headers)
    assert response.status_code == 403
