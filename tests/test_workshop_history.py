"""Phase 5: customer and vehicle workshop history. Read-only; disposable tenant databases only."""
import sys

import pytest

from app.services import job_cards as jc
from app.services import job_conversion as jconv
from app.services import workshop_history as wh
from app.tenant_db import get_conn


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    return tmp_path


def seed(tid):
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Thabo Mokoena', '0821234567')")
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Lerato Dlamini', '0839990000')")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 10, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', 'Four wheels', 150, 1)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Alignment', '', 450, 1)")
    conn.commit()
    conn.close()


def job(tid, customer_id, vehicle_id, opened_day, items=None, technician="Sipho", work="Balanced", mileage=80000):
    created = jc.create_job(tid, {"customer_id": customer_id, "vehicle_id": vehicle_id, "mileage_in": mileage,
                                  "technician": technician, "work_performed": work,
                                  "items": items or [{"item_type": "service", "service_id": 1, "quantity": 1}]},
                            "seed", True)
    conn = get_conn(tid)
    conn.execute("UPDATE job_cards SET opened_at=? WHERE id=?", (f"2026-10-{opened_day:02d} 09:00:00", created["id"]))
    conn.commit()
    conn.close()
    return created


def counts(tid):
    conn = get_conn(tid)
    value = (conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
             conn.execute("SELECT COUNT(*) FROM stock_history").fetchone()[0],
             conn.execute("SELECT current_stock FROM products WHERE id=1").fetchone()[0])
    conn.close()
    return value


# ── Vehicle search and history ──────────────────────────────────────────────

def test_search_finds_vehicles_by_registration_in_any_format(data_dir):
    seed(10)
    jc.upsert_vehicle(10, 1, "CA 123 456", make="Toyota", model="Corolla", year=2015)
    for term in ("CA123456", "ca 123-456", "123 456", "CA-123"):
        results = wh.search_vehicles(10, term)
        assert [r["registration"] for r in results] == ["CA123456"], term
    assert wh.search_vehicles(10, "CA123456")[0]["owner"] == "Thabo Mokoena"


def test_empty_and_unknown_searches(data_dir):
    seed(10)
    assert wh.search_vehicles(10, "") == [] and wh.search_vehicles(10, "   ") == []
    assert wh.search_vehicles(10, "ZZZ999") == []


@pytest.mark.parametrize("term", ["CA<script>", "CA';DROP", "CA%_", "ABCDEFGHIJKLMNOP", "--"])
def test_invalid_registration_searches_are_refused(data_dir, term):
    seed(10)
    with pytest.raises(jc.JobCardError):
        wh.search_vehicles(10, term)


def test_vehicle_history_newest_first_by_default_and_oldest_on_request(data_dir):
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    a = job(10, 1, vid, 1, work="First visit")
    b = job(10, 1, vid, 9, work="Second visit")
    c = job(10, 1, vid, 20, work="Third visit")
    newest = wh.vehicle_history(10, vid)
    assert [j["job_number"] for j in newest["jobs"]] == [c["job_number"], b["job_number"], a["job_number"]]
    assert newest["order"] == "newest"
    oldest = wh.vehicle_history(10, vid, "oldest")
    assert [j["job_number"] for j in oldest["jobs"]] == [a["job_number"], b["job_number"], c["job_number"]]
    assert wh.vehicle_history(10, vid, "sideways")["order"] == "newest"


def test_vehicle_history_shows_services_tyres_mileage_technician_and_status(data_dir):
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    created = job(10, 1, vid, 5, items=[{"item_type": "product", "product_id": 1, "quantity": 4},
                                        {"item_type": "service", "service_id": 1, "quantity": 1},
                                        {"item_type": "service", "service_id": 2, "quantity": 1}],
                  technician="Sipho", mileage=91000)
    row = wh.vehicle_history(10, vid)["jobs"][0]
    assert row["job_number"] == created["job_number"]
    assert row["services"] == ["Balancing (Four wheels)", "Alignment"]
    assert row["tyres"] == ["Touring 205 (Brand A 205/55R16 Touring)"]
    assert row["mileage_in"] == 91000 and row["technician"] == "Sipho" and row["status"] == "open"
    assert row["invoice_number"] is None


def test_repeated_item_lines_are_grouped_with_a_count(data_dir):
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    job(10, 1, vid, 5, items=[{"item_type": "service", "service_id": 1, "quantity": 1}] * 3
        + [{"item_type": "product", "product_id": 1, "quantity": 1}])
    row = wh.vehicle_history(10, vid)["jobs"][0]
    assert row["services"] == ["Balancing (Four wheels) ×3"]
    assert row["tyres"] == ["Touring 205 (Brand A 205/55R16 Touring)"]


def test_invoice_links_are_correct_and_only_read(data_dir):
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    invoiced = job(10, 1, vid, 3, items=[{"item_type": "product", "product_id": 1, "quantity": 1}])
    jc.set_status(10, invoiced["id"], "in_progress", "seed")
    jc.set_status(10, invoiced["id"], "ready", "seed")
    result = jconv.convert_job(10, invoiced["id"], "owner", "card", can_convert=True, is_admin=True, payment_confirmed=True)
    plain = job(10, 1, vid, 7)
    before = counts(10)
    rows = {r["job_number"]: r for r in wh.vehicle_history(10, vid)["jobs"]}
    assert counts(10) == before, "history must not change invoices, stock or stock history"
    assert rows[invoiced["job_number"]]["invoice_number"] == result["invoice_number"]
    assert rows[invoiced["job_number"]]["invoice_id"] == result["invoice_id"]
    assert rows[invoiced["job_number"]]["payment_method"] == "card"
    assert rows[plain["job_number"]]["invoice_number"] is None


def test_vehicle_with_no_jobs_has_an_empty_history(data_dir):
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "EMPTY01", make="", model="", year=None)["id"]
    data = wh.vehicle_history(10, vid)
    assert data["jobs"] == [] and data["vehicle"]["make"] == ""


def test_invalid_and_unknown_ids_are_handled(data_dir):
    seed(10)
    for bad in ("abc", 0, -3, None, ""):
        with pytest.raises(jc.JobCardError):
            wh.vehicle_history(10, bad)
        with pytest.raises(jc.JobCardError):
            wh.customer_workshop_history(10, bad)
    assert wh.vehicle_history(10, 999) is None
    assert wh.customer_workshop_history(10, 999) is None


# ── Customers with several vehicles ─────────────────────────────────────────

def test_customer_with_multiple_vehicles_sees_all_vehicles_and_jobs(data_dir):
    seed(10)
    v1 = jc.upsert_vehicle(10, 1, "AAA111", make="Toyota", model="Corolla", year=2015)["id"]
    v2 = jc.upsert_vehicle(10, 1, "BBB222", make="VW", model="Polo", year=2019)["id"]
    jc.upsert_vehicle(10, 2, "CCC333", make="", model="", year=None)
    job(10, 1, v1, 2)
    job(10, 1, v2, 8)
    job(10, 1, v1, 15)
    data = wh.customer_workshop_history(10, 1)
    assert [v["registration"] for v in data["vehicles"]] == ["AAA111", "BBB222"]
    assert len(data["jobs"]) == 3
    assert [j["registration"] for j in data["jobs"]] == ["AAA111", "BBB222", "AAA111"]  # newest first
    assert all(j["registration"] != "CCC333" for j in data["jobs"])
    assert wh.customer_workshop_history(10, 1, "oldest")["jobs"][0]["opened_at"].startswith("2026-10-02")


def test_customer_without_vehicles_or_jobs_has_empty_history(data_dir):
    seed(10)
    data = wh.customer_workshop_history(10, 2)
    assert data["vehicles"] == [] and data["jobs"] == []


# ── Tenant isolation ────────────────────────────────────────────────────────

def test_other_tenants_never_see_vehicles_or_jobs(data_dir):
    seed(10)
    seed(11)
    vid = jc.upsert_vehicle(10, 1, "TENANT1", make="Toyota", model="Corolla", year=2015)["id"]
    job(10, 1, vid, 4)
    assert wh.search_vehicles(11, "TENANT1") == []
    assert wh.vehicle_history(11, vid) is None
    assert wh.customer_workshop_history(11, 1)["jobs"] == []
    assert [v["registration"] for v in wh.customer_workshop_history(11, 1)["vehicles"]] == []


# ── Routes, login, escaping and layout ──────────────────────────────────────

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


def add_tenant(tid, username):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("INSERT INTO tenants (id, business_name, username, password_hash, is_admin) VALUES (?,?,?,?,0)",
                 (tid, f"Shop {tid}", username, "not-a-real-password"))
    conn.commit()
    conn.close()


def log_in(client, tid):
    with client.session_transaction() as session:
        session["_user_id"] = str(tid)
        session["_fresh"] = True


def test_history_routes_require_login(client):
    for path in ("/vehicles/", "/vehicles/1"):
        response = client.get(path)
        assert response.status_code == 302 and "login" in response.headers["Location"]
    assert client.get("/customers/1").status_code == 302


def test_search_page_and_results_render_for_staff(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)
    empty = client.get("/vehicles/").get_data(as_text=True)
    assert "Search a registration to see its jobs" in empty
    found = client.get("/vehicles/?q=CA+123").get_data(as_text=True)
    assert "CA123456" in found and "Thabo Mokoena" in found and "Toyota Corolla 2015" in found
    missing = client.get("/vehicles/?q=NOPE999").get_data(as_text=True)
    assert "No vehicle matches" in missing


def test_invalid_search_shows_a_message_not_an_error(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    response = client.get("/vehicles/?q=CA%3Cscript%3E")
    assert response.status_code == 200
    assert "Enter a registration using letters" in response.get_data(as_text=True)


def test_vehicle_history_page_lists_jobs_newest_first_and_escapes_text(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    job(10, 1, vid, 2, technician="<script>alert(1)</script>", work="Rotated & balanced")
    job(10, 1, vid, 12, work="Second")
    html = client.get(f"/vehicles/{vid}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "Rotated &amp; balanced" in html
    assert html.index("JOB-000002") < html.index("JOB-000001")  # newest first
    oldest = client.get(f"/vehicles/{vid}?order=oldest").get_data(as_text=True)
    assert oldest.index("JOB-000001") < oldest.index("JOB-000002")
    assert "Owner" in html and "Thabo Mokoena" in html


def test_vehicle_history_empty_state_and_unknown_vehicle(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "NEW0001", make="", model="", year=None)["id"]
    html = client.get(f"/vehicles/{vid}").get_data(as_text=True)
    assert "No job cards yet" in html and "Make, model and year not recorded" in html
    redirected = client.get("/vehicles/9999")
    assert redirected.status_code == 302 and redirected.headers["Location"].endswith("/vehicles/")


def test_other_tenant_cannot_open_a_vehicle_history(app, client):
    add_tenant(10, "owner-a")
    add_tenant(11, "owner-b")
    log_in(client, 10)
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "PRIVATE1", make="Toyota", model="Corolla", year=2015)["id"]
    log_in(client, 11)
    assert client.get(f"/vehicles/{vid}").status_code == 302
    search = client.get("/vehicles/?q=PRIVATE1").get_data(as_text=True)
    assert "No vehicle matches" in search and "Owner:" not in search  # the echoed search term is not data


def test_customer_profile_shows_workshop_history_section(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    job(10, 1, vid, 6, technician="Sipho", work="Balanced")
    html = client.get("/customers/1").get_data(as_text=True)
    assert 'id="workshop-history"' in html and "Workshop History" in html
    assert f"/vehicles/{vid}" in html and "CA123456" in html and "JOB-000001" in html
    assert "Not invoiced" in html and "Newest first" in html
    oldest = client.get("/customers/1?order=oldest").get_data(as_text=True)
    assert "Oldest first" in oldest


def test_customer_profile_without_workshop_history_shows_empty_states(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    html = client.get("/customers/2").get_data(as_text=True)
    assert "No vehicles recorded for this customer yet" in html and "No job cards yet" in html


def test_history_pages_use_responsive_card_layout(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    seed(10)
    vid = jc.upsert_vehicle(10, 1, "CA123456", make="Toyota", model="Corolla", year=2015)["id"]
    job(10, 1, vid, 6)
    html = client.get(f"/vehicles/{vid}").get_data(as_text=True)
    assert "col-12 col-md-6" in html and "btn-group" in html
    assert 'name="viewport"' in html
