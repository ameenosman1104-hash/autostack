"""Job cards Phase 2 UI: routes, permissions, CSRF, tenant isolation, responsive markup and escaping.

Each test uses a fresh app module and a temporary data folder. No stock is moved and no invoice is created.
"""
import re
import sys

import pytest

from app.services import job_cards as jc
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


@pytest.fixture
def staff(client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    return 10


def set_admin(tid, flag):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("UPDATE tenants SET is_admin=? WHERE id=?", (1 if flag else 0, tid))
    conn.commit()
    conn.close()


def seed(tid):
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone, email) VALUES ('Thabo Mokoena', '0821234567', 'thabo@example.com')")
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Lerato Dlamini', '0839990000')")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 5, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', 'Four wheels', 150, 1)")
    conn.commit()
    conn.close()
    v = jc.upsert_vehicle(tid, 1, "CA 123 456", make="Toyota", model="Corolla", year=2015, actor="seed")
    return v["id"]


def job(tid, vehicle_id, items=None):
    return jc.create_job(tid, {"customer_id": 1, "vehicle_id": vehicle_id, "mileage_in": 84000,
                               "items": items or [{"item_type": "service", "service_id": 1, "quantity": 1}]},
                         "seed", True)


def stock(tid, pid=1):
    conn = get_conn(tid)
    value = conn.execute("SELECT current_stock FROM products WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    return value


def counts(tid):
    conn = get_conn(tid)
    result = (conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
              conn.execute("SELECT COUNT(*) FROM stock_history").fetchone()[0])
    conn.close()
    return result


# â”€â”€ Access control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("method,path", [("get", "/jobs/"), ("get", "/jobs/new"), ("get", "/jobs/1"),
                                         ("post", "/jobs/save"), ("post", "/jobs/1/status"), ("get", "/jobs/api/customers")])
def test_every_job_route_requires_login(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code in (302, 401)
    assert "login" in response.headers.get("Location", "login")


def test_sidebar_has_job_cards_link_with_wrench_icon(staff, client):
    html = client.get("/jobs/").get_data(as_text=True)
    assert re.search(r'<i class="bi bi-wrench me-2"></i>\s*Job Cards', html)
    assert 'href="/jobs/"' in html


def test_staff_cannot_cancel_by_default_but_admin_can(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    response = client.post(f"/jobs/{j['id']}/status", json={"status": "cancelled"})
    assert response.status_code == 400
    assert "not authorised to cancel" in response.get_json()["error"]
    assert "Cancel job" not in client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    set_admin(staff, True)
    assert client.post(f"/jobs/{j['id']}/status", json={"status": "cancelled"}).status_code == 200


def test_staff_can_cancel_when_admin_enables_it(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    save_setting(staff, "job_staff_can_cancel", "1")
    assert client.post(f"/jobs/{j['id']}/status", json={"status": "cancelled"}).status_code == 200


def test_staff_cannot_change_price_through_the_ui(staff, client):
    vid = seed(staff)
    response = client.post("/jobs/save", json={"customer_id": 1, "vehicle_id": vid, "items": [
        {"item_type": "service", "service_id": 1, "quantity": 1, "unit_price": 5}]})
    assert response.status_code == 400
    assert "not authorised" in response.get_json()["error"]


def test_product_api_hides_stock_levels_from_staff(staff, client):
    seed(staff)
    rows = client.get("/jobs/api/products?q=Touring").get_json()
    assert rows and "current_stock" not in rows[0]
    set_admin(staff, True)
    rows = client.get("/jobs/api/products?q=Touring").get_json()
    assert rows[0]["current_stock"] == 5


# â”€â”€ CSRF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_post_requires_csrf_token_when_protection_is_enabled(app, staff, client):
    seed(staff)
    app.config["WTF_CSRF_ENABLED"] = True
    body = {"customer_id": 1, "vehicle_id": 1, "items": [{"item_type": "service", "service_id": 1, "quantity": 1}]}
    assert client.post("/jobs/save", json=body).status_code == 400
    page = client.get("/jobs/new").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    response = client.post("/jobs/save", json=body, headers={"X-CSRFToken": token})
    assert response.status_code == 201


# â”€â”€ Tenant isolation at route level â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_other_tenant_cannot_open_or_change_a_job(client, app):
    add_tenant(10, "owner-a")
    add_tenant(11, "owner-b")
    log_in(client, 10)
    vid = seed(10)
    j = job(10, vid)
    log_in(client, 11)
    assert client.get(f"/jobs/{j['id']}").status_code == 302
    assert client.post(f"/jobs/{j['id']}/status", json={"status": "in_progress"}).status_code == 400
    assert client.get("/jobs/").get_data(as_text=True).count(j["job_number"]) == 0


# â”€â”€ Dashboard, search and forms â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_dashboard_filters_by_status_and_search(staff, client):
    vid = seed(staff)
    a = job(staff, vid)
    job(staff, vid)
    jc.set_status(staff, a["id"], "in_progress", "seed")
    assert a["job_number"] in client.get("/jobs/?status=in_progress").get_data(as_text=True)
    assert "No job cards match" in client.get("/jobs/?q=ZZZ999").get_data(as_text=True)
    assert "CA123456" in client.get("/jobs/?q=CA123456").get_data(as_text=True)


def test_list_is_responsive_and_has_status_filter(staff, client):
    html = client.get("/jobs/").get_data(as_text=True)
    assert "table-responsive" in html and "d-none d-md-table-cell" in html
    assert 'name="status"' in html and 'name="q"' in html
    assert 'name="viewport"' in client.get("/quotations/").get_data(as_text=True)


def test_new_form_renders_with_customer_vehicle_and_items(staff, client):
    seed(staff)
    html = client.get("/jobs/new").get_data(as_text=True)
    assert "Create job card" in html and 'id="vehicleSelect"' in html and 'id="serviceSelect"' in html
    assert 'id="technician"' in html and 'id="mileageIn"' in html and 'id="workPerformed"' in html
    assert 'id="itemCards"' in html and 'd-md-none' in html and 'd-none d-md-block' in html


def test_create_and_edit_through_the_routes(staff, client):
    vid = seed(staff)
    before = counts(staff)
    response = client.post("/jobs/save", json={"customer_id": 1, "vehicle_id": vid, "mileage_in": 90000,
                                               "technician": "Sipho", "work_performed": "Balance and align",
                                               "items": [{"item_type": "service", "service_id": 1, "quantity": 1},
                                                         {"item_type": "product", "product_id": 1, "quantity": 2}]})
    assert response.status_code == 201
    body = response.get_json()
    assert body["job_number"] == "JOB-000001" and body["url"] == f"/jobs/{body['id']}"
    assert body["warnings"] == []
    edit = client.post(f"/jobs/{body['id']}/save", json={"customer_id": 1, "vehicle_id": vid, "mileage_in": 90100,
                                                          "items": [{"item_type": "service", "service_id": 1, "quantity": 2}]})
    assert edit.status_code == 200
    assert jc.get_job(staff, body["id"])["header"]["total"] == 300.0
    assert counts(staff) == before
    assert stock(staff) == 5


def test_edit_page_refused_for_ready_job(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    jc.set_status(staff, j["id"], "in_progress", "seed")
    jc.set_status(staff, j["id"], "ready", "seed")
    response = client.get(f"/jobs/{j['id']}/edit")
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/jobs/{j['id']}")


def test_view_shows_items_technician_mileage_and_audit_history(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    client.post(f"/jobs/{j['id']}/technician", json={"technician": "Sipho"})
    client.post(f"/jobs/{j['id']}/status", json={"status": "in_progress"})
    html = client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    assert "Sipho" in html and "84000" in html and "Balancing" in html
    assert "Status: in progress" in html and "created" in html
    assert "Start work" not in html  # already in progress
    assert "Mark ready for collection" in html


def test_status_and_technician_routes_validate(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    assert client.post(f"/jobs/{j['id']}/status", json={"status": "completed"}).status_code == 400
    assert client.post(f"/jobs/{j['id']}/status", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/jobs/{j['id']}/technician", json={"technician": "Sipho"}).status_code == 200
    jc.set_status(staff, j["id"], "in_progress", "seed")
    jc.set_status(staff, j["id"], "ready", "seed")
    jc.set_status(staff, j["id"], "completed", "seed")
    assert client.post(f"/jobs/{j['id']}/technician", json={"technician": "Other"}).status_code == 400


def test_mileage_out_recorded_on_collection(staff, client):
    vid = seed(staff)
    j = job(staff, vid)
    client.post(f"/jobs/{j['id']}/status", json={"status": "in_progress"})
    client.post(f"/jobs/{j['id']}/status", json={"status": "ready"})
    response = client.post(f"/jobs/{j['id']}/status", json={"status": "completed", "mileage_out": 84300})
    assert response.status_code == 200
    assert jc.get_job(staff, j["id"])["header"]["mileage_out"] == 84300


def test_user_text_is_escaped_in_pages(staff, client):
    vid = seed(staff)
    payload = "<script>alert('x')</script>"
    j = jc.create_job(staff, {"customer_id": 1, "vehicle_id": vid, "work_performed": payload,
                              "items": [{"item_type": "service", "service_id": 1, "quantity": 1}]}, "seed", True)
    html = client.get(f"/jobs/{j['id']}").get_data(as_text=True)
    assert payload not in html
    assert "&lt;script&gt;" in html


# â”€â”€ Customers and vehicles through the API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_customer_and_vehicle_api(staff, client):
    seed(staff)
    names = [c["name"] for c in client.get("/jobs/api/customers?q=Thabo").get_json()]
    assert names == ["Thabo Mokoena"]
    vehicles = client.get("/jobs/api/vehicles?customer_id=1").get_json()
    assert [v["registration"] for v in vehicles] == ["CA123456"]
    created = client.post("/jobs/api/vehicles", json={"customer_id": 1, "registration": "ZZ 999 ZZ", "make": "VW"})
    assert created.status_code == 201 and created.get_json()["registration"] == "ZZ999ZZ"
    clash = client.post("/jobs/api/vehicles", json={"customer_id": 2, "registration": "ZZ999ZZ"})
    assert clash.status_code == 400 and "another customer" in clash.get_json()["error"]


def test_api_customers_requires_query_and_returns_empty_list(staff, client):
    seed(staff)
    assert client.get("/jobs/api/vehicles").get_json() == []
    assert client.get("/jobs/api/products").get_json() == []


def test_no_invoices_or_stock_movement_from_any_ui_path(staff, client):
    vid = seed(staff)
    before = counts(staff)
    j = client.post("/jobs/save", json={"customer_id": 1, "vehicle_id": vid,
                                        "items": [{"item_type": "product", "product_id": 1, "quantity": 2}]}).get_json()
    client.post(f"/jobs/{j['id']}/status", json={"status": "in_progress"})
    client.post(f"/jobs/{j['id']}/status", json={"status": "ready"})
    client.post(f"/jobs/{j['id']}/status", json={"status": "completed"})
    assert counts(staff) == before
    assert stock(staff) == 5
