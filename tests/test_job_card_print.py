"""Job card printing and WhatsApp (Phase 3): PDF, print view, phone formatting, access control and escaping.

Nothing here sends a WhatsApp message. Links are checked only as text.
"""
import io
import re
import sys
from urllib.parse import unquote

import pytest
from pypdf import PdfReader

from app.db_migrations import LATEST_SCHEMA_VERSION, migrate_tenant_db
from app.services import job_card_output as jco
from app.services import job_cards as jc
from app.services import quotation_output as qo
from app.tenant_db import get_conn, save_setting


@pytest.fixture
def app(tmp_path, monkeypatch):
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


def add_tenant(tid, username, admin=False):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("INSERT INTO tenants (id, business_name, username, password_hash, is_admin) VALUES (?,?,?,?,?)",
                 (tid, f"Shop {tid}", username, "not-a-real-password", 1 if admin else 0))
    conn.commit()
    conn.close()


def log_in(client, tid):
    with client.session_transaction() as session:
        session["_user_id"] = str(tid)
        session["_fresh"] = True


def set_admin(tid, flag):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("UPDATE tenants SET is_admin=? WHERE id=?", (1 if flag else 0, tid))
    conn.commit()
    conn.close()


@pytest.fixture
def staff(client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    return 10


def seed(tid, phone="0821234567"):
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone, email) VALUES ('Thabo Mokoena', ?, 'thabo@example.com')", (phone,))
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 5, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', 'Four wheels', 150, 1)")
    conn.commit()
    conn.close()
    return jc.upsert_vehicle(tid, 1, "CA 123 456", make="Toyota", model="Corolla", year=2015, actor="seed")["id"]


def job(tid, vehicle_id, **extra):
    data = {"customer_id": 1, "vehicle_id": vehicle_id, "mileage_in": 84000, "technician": "Sipho",
            "customer_complaint": "Vibration at 100 km/h", "work_performed": "Balanced all four wheels",
            "items": [{"item_type": "service", "service_id": 1, "quantity": 1}]}
    data.update(extra)
    return jc.create_job(tid, data, "seed", True)


def shop(country="27", logo=""):
    return {"name": "Tyre King", "address": "1 Main Rd", "phone": "011 000 0000", "email": "shop@example.com",
            "logo_path": logo, "currency": "R", "country_code": country}


def pdf_text(content):
    reader = PdfReader(io.BytesIO(content))
    return reader, "\n".join(page.extract_text() or "" for page in reader.pages)


# ── Phone formatting ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("082 123 4567", "27821234567"),
    ("082-123-4567", "27821234567"),
    ("(082) 123 4567", "27821234567"),
    ("+27 82 123 4567", "27821234567"),
    ("+27 (0)82 123 4567", "27821234567"),
    ("0027 82 123 4567", "27821234567"),
    ("27821234567", "27821234567"),
    ("0821234567", "27821234567"),
])
def test_south_african_numbers_normalised(raw, expected):
    assert jco.normalise_phone(raw, "27") == expected


@pytest.mark.parametrize("raw", ["", "   ", "12345", "082 123", "+27 82 123 45678", "0821234567ABC", "27 82 12"])
def test_invalid_south_african_numbers_refused(raw):
    with pytest.raises(jco.PhoneError):
        jco.normalise_phone(raw, "27")


def test_local_number_needs_country_code_setting():
    with pytest.raises(jco.PhoneError, match="country code"):
        jco.normalise_phone("0821234567", "")


def test_international_number_with_plus_is_kept():
    assert jco.normalise_phone("+44 20 7946 0958", "27") == "442079460958"


# ── WhatsApp link ───────────────────────────────────────────────────────────

def test_whatsapp_link_has_job_number_status_and_registration(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    detail = jc.get_job(staff, j["id"])
    url = jco.job_whatsapp_url(detail, shop())
    assert url.startswith("https://wa.me/27821234567?text=")
    text = unquote(url.split("?text=", 1)[1])
    assert j["job_number"] in text and "Status: Open" in text and "CA123456" in text and "Tyre King" in text


def test_whatsapp_status_text_follows_the_job_status(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    jc.set_status(staff, j["id"], "in_progress", "seed")
    jc.set_status(staff, j["id"], "ready", "seed")
    text = unquote(jco.job_whatsapp_url(jc.get_job(staff, j["id"]), shop()).split("?text=", 1)[1])
    assert "Status: Ready for collection" in text and "ready for collection" in text


def test_whatsapp_uses_the_current_saved_phone_number(staff, client):
    vid = seed(staff, phone="0821111111")
    j = job(staff, vid)
    conn = get_conn(staff)
    conn.execute("UPDATE customers SET phone='0839999999' WHERE id=1")
    conn.commit()
    conn.close()
    assert jco.job_whatsapp_url(jc.get_job(staff, j["id"]), shop()).startswith("https://wa.me/27839999999?")


def test_missing_phone_shows_a_clear_message_and_disables_the_button(staff, client):
    vid = seed(staff, phone="")
    j = job(staff, vid)
    html = client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    assert "Add the customer&#39;s phone number" in html or "Add the customer's phone number" in html
    assert "disabled" in html and 'id="waUpdateBtn"' not in html


def test_view_offers_print_pdf_and_whatsApp_actions(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    html = client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    assert f"/jobs/{j['id']}/print?autoprint=1" in html
    assert f"/jobs/{j['id']}/pdf" in html
    assert 'id="waUpdateBtn"' in html and "https://wa.me/27821234567" in html.replace("&amp;", "&")


def test_whatsapp_is_a_link_only_and_nothing_is_marked_sent(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    html = client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    assert "window.open" in html  # the button opens a chat; it does not call any sending endpoint
    assert "Message sent" not in html and "message was delivered" not in html.lower()
    assert "/whatsapp" not in html  # no server-side send route exists
    events = [e["event"] for e in jc.get_job(staff, j["id"])["events"]]
    assert not any("whatsapp" in e.lower() or "sent" in e.lower() for e in events)


# ── PDF ─────────────────────────────────────────────────────────────────────

def test_pdf_is_a_valid_a4_document_with_the_key_details(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    response = client.get(f"/jobs/{j['id']}/pdf")
    assert response.status_code == 200 and response.data.startswith(b"%PDF")
    assert response.headers["Content-Disposition"] == f'attachment; filename="{j["job_number"]}.pdf"'
    reader, text = pdf_text(response.data)
    assert len(reader.pages) == 1
    assert round(float(reader.pages[0].mediabox.width)) == 595 and round(float(reader.pages[0].mediabox.height)) == 842
    for needle in (j["job_number"], "CA123456", "Toyota", "Corolla", "84000", "Thabo Mokoena", "Balancing",
                   "Vibration at 100 km/h", "Balanced all four wheels", "Customer signature", "Technician signature"):
        assert needle in text, needle


def test_pdf_spans_multiple_pages_and_repeats_no_data_loss(staff, client):
    vid = seed(staff)
    items = [{"item_type": "service", "service_id": 1, "quantity": 1, "unit_price": 150 + n} for n in range(70)]
    j = job(staff, vid, items=items)
    reader, text = pdf_text(jco.render_job_card_pdf(jc.get_job(staff, j["id"]), shop()))
    assert len(reader.pages) >= 2
    assert "Description" in text  # table header is present
    assert text.count("Balancing") == 70


def test_long_unbroken_text_wraps_without_error(staff, client):
    vid = seed(staff)
    long_word = "W" * 1500
    j = job(staff, vid, work_performed=long_word + " end", customer_complaint=" ".join(["lorem"] * 300))
    response = client.get(f"/jobs/{j['id']}/pdf")
    assert response.status_code == 200
    reader, text = pdf_text(response.data)
    assert "end" in text and len(reader.pages) >= 1


def test_missing_logo_and_incomplete_vehicle_still_print(staff, client):
    vid = seed(staff)
    conn = get_conn(staff)
    conn.execute("UPDATE vehicles SET make='', model='', year=NULL WHERE id=?", (vid,))
    conn.commit()
    conn.close()
    j = job(staff, vid, mileage_in=None, technician="", customer_complaint="", promised_date=None)
    content = jco.render_job_card_pdf(jc.get_job(staff, j["id"]), shop(logo="does/not/exist.png"))
    _, text = pdf_text(content)
    assert content.startswith(b"%PDF") and "Not recorded" in text and "Not assigned" in text


def test_logo_must_resolve_inside_static(staff, client):
    assert qo.logo_file("../../secret.png") is None
    assert qo.logo_file("") is None
    assert qo.logo_file("missing.png") is None


def test_user_text_is_escaped_in_pdf_and_print(staff, client):
    payload = "<b>Bold</b> & <script>x</script>"
    add_tenant(11, "other-owner", admin=False)
    vid = seed(staff)
    j = job(staff, vid, customer_complaint=payload, work_performed="A & B <i>ok</i>")
    _, text = pdf_text(client.get(f"/jobs/{j['id']}/pdf").data)
    assert "Bold" in text and "A & B" in text and "<script>x</script>" in text
    html = client.get(f"/jobs/{j['id']}/print").get_data(as_text=True)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_pdf_customer_name_is_escaped(staff, client):
    vid = seed(staff)
    conn = get_conn(staff)
    conn.execute("UPDATE customers SET name='Smith & <Sons>' WHERE id=1")
    conn.commit()
    conn.close()
    j = job(staff, vid)
    content = client.get(f"/jobs/{j['id']}/pdf").data
    _, text = pdf_text(content)
    assert "Smith & <Sons>" in text


# ── Print view ──────────────────────────────────────────────────────────────

def test_print_view_hides_navigation_and_fits_a4(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    html = client.get(f"/jobs/{j['id']}/print").get_data(as_text=True)
    assert "@page { size: A4" in html and "@media print" in html
    assert "class=\"navbar" not in html and "Logout" not in html and "sidebar" not in html
    assert "no-print" in html
    assert j["job_number"] in html and "Customer signature" in html and "Technician signature" in html
    assert "autoprint" in html


def test_print_view_autoprint_parameter_is_supported(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    html = client.get(f"/jobs/{j['id']}/print?autoprint=1").get_data(as_text=True)
    assert "window.print()" in html


# ── Access control and tenant isolation ─────────────────────────────────────

@pytest.mark.parametrize("path", ["/jobs/1/print", "/jobs/1/pdf"])
def test_print_and_pdf_require_login(client, path):
    response = client.get(path)
    assert response.status_code in (302, 401)
    assert "login" in response.headers.get("Location", "login")


@pytest.mark.parametrize("path", ["print", "pdf"])
def test_other_tenant_cannot_print_or_download(client, app, path):
    add_tenant(10, "owner-a")
    add_tenant(11, "owner-b")
    log_in(client, 10)
    vid = seed(10)
    j = job(10, vid)
    log_in(client, 11)
    assert client.get(f"/jobs/{j['id']}/{path}").status_code == 404


def test_staff_and_admin_can_print(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    assert client.get(f"/jobs/{j['id']}/pdf").status_code == 200
    set_admin(staff, True)
    assert client.get(f"/jobs/{j['id']}/print").status_code == 200


def test_missing_job_returns_404(staff, client):
    assert client.get("/jobs/9999/pdf").status_code == 404
    assert client.get("/jobs/9999/print").status_code == 404


# ── Schema, persistence and mobile entry point ──────────────────────────────

def test_migration_27_adds_complaint_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "30.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(30, str(path))
    import sqlite3
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == LATEST_SCHEMA_VERSION == 30
    conn.execute("DELETE FROM schema_migrations WHERE version>=27")
    conn.commit()
    conn.close()
    migrate_tenant_db(30, str(path))
    migrate_tenant_db(30, str(path))
    conn = sqlite3.connect(path)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(job_cards)")]
    assert columns.count("customer_complaint") == 1
    conn.close()


def test_complaint_is_saved_and_returned(staff, client):
    vid = seed(staff)
    j = job(staff, vid, customer_complaint="Pulling to the left")
    assert jc.get_job(staff, j["id"])["header"]["customer_complaint"] == "Pulling to the left"
    jc.update_job(staff, j["id"], {"customer_id": 1, "vehicle_id": vid, "customer_complaint": "Noise at speed",
                                   "items": [{"item_type": "service", "service_id": 1, "quantity": 1}]}, "seed", True)
    assert jc.get_job(staff, j["id"])["header"]["customer_complaint"] == "Noise at speed"


def test_dashboard_has_job_cards_entry_for_mobile_users(staff, client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/jobs/"' in html and "Job Cards" in html
