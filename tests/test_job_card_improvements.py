"""Job Cards improvements: customer choice, optional mileage, technicians, services and tenant isolation."""
import re
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import add_customer, add_product, add_service, complete_sale, get_conn, init_tenant_db
from app.services import job_cards as jc
from app.services import service_catalogue as cat
from app.services import technicians as tech

PASSWORD = "improvements-password"


def _tenant(name):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Garage", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    return tid, username


@pytest.fixture
def shop():
    tid, username = _tenant("alpha")
    customer = add_customer(tid, "Thabo Mokoena", phone="082 123 4567")
    vehicle = jc.upsert_vehicle(tid, customer, "CA123456", make="VW", model="Polo")["id"]
    service = add_service(tid, "Wheel Alignment", default_price=350)
    product = add_product(tid, "TYRE-1", "Widget Tyre", current_stock=10, selling_price=100)
    return {"tid": tid, "username": username, "customer": customer, "vehicle": vehicle,
            "service": service, "product": product}


def _job(tid, customer, vehicle, service, **extra):
    data = {"customer_id": customer, "vehicle_id": vehicle,
            "items": [{"item_type": "service", "service_id": service, "quantity": 1}]}
    data.update(extra)
    return jc.create_job(tid, data, "clerk", False)


def _count(tid, table):
    conn = get_conn(tid)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ── Customer selection ───────────────────────────────────────────────────────────

class TestExistingCustomer:
    def test_existing_customer_stores_the_correct_id(self, shop):
        result = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"])
        job = jc.get_job(shop["tid"], result["id"])
        assert job["header"]["customer_id"] == shop["customer"]
        assert job["header"]["customer_name"] == "Thabo Mokoena"

    def test_existing_customer_does_not_create_a_customer(self, shop):
        before = _count(shop["tid"], "customers")
        _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"])
        assert _count(shop["tid"], "customers") == before


class TestNewCustomer:
    def test_new_customer_is_created_and_linked_to_the_job(self, shop):
        before = _count(shop["tid"], "customers")
        result = jc.create_job(shop["tid"], {
            "new_customer": {"name": "Lerato Dube", "phone": "071 555 0199"},
            "new_vehicle": {"registration": "NB99871", "make": "Toyota", "model": "Corolla"},
            "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}],
        }, "clerk", False)
        job = jc.get_job(shop["tid"], result["id"])
        assert _count(shop["tid"], "customers") == before + 1
        assert job["header"]["customer_name"] == "Lerato Dube"
        assert job["header"]["customer_phone"] == "071 555 0199"
        vehicle = jc.get_vehicle(shop["tid"], job["header"]["vehicle_id"])
        assert vehicle["registration"] == "NB99871"
        assert vehicle["customer_id"] == job["header"]["customer_id"]

    def test_new_customer_creation_is_atomic_with_the_job(self, shop):
        """A job refused for another reason must not leave a stray customer behind."""
        before = _count(shop["tid"], "customers")
        with pytest.raises(jc.JobCardError):
            jc.create_job(shop["tid"], {
                "new_customer": {"name": "Ghost Customer", "phone": "071 555 0000"},
                "new_vehicle": {"registration": "GH00001"},
                "items": [],
            }, "clerk", False)
        assert _count(shop["tid"], "customers") == before

    @pytest.mark.parametrize("data, message", [
        ({"customer_id": 1, "new_customer": {"name": "X", "phone": "0711111111"}}, "not both"),
        ({}, "Choose an existing customer or add a new customer"),
    ])
    def test_exactly_one_customer_option_is_required(self, shop, data, message):
        payload = {"vehicle_id": shop["vehicle"], "new_vehicle": {"registration": "ZZ11111"},
                   "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}], **data}
        if "customer_id" in data:
            payload["customer_id"] = shop["customer"]
        with pytest.raises(jc.JobCardError, match=message):
            jc.create_job(shop["tid"], payload, "clerk", False)

    @pytest.mark.parametrize("new_customer", [
        {"name": "", "phone": "0711111111"},
        {"name": "No Phone", "phone": ""},
        {"name": "Short Phone", "phone": "12"},
        {"name": "Bad Phone", "phone": "call me"},
    ])
    def test_new_customer_validation(self, shop, new_customer):
        with pytest.raises(jc.JobCardError):
            jc.create_job(shop["tid"], {"new_customer": new_customer,
                                        "new_vehicle": {"registration": "VA00001"},
                                        "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                          "clerk", False)

    def test_duplicate_phone_is_refused_whatever_the_formatting(self, shop):
        before_customers = _count(shop["tid"], "customers")
        before_jobs = _count(shop["tid"], "job_cards")
        for phone in ("0821234567", "+27 82 123 4567", "082-123-4567"):
            with pytest.raises(jc.JobCardError, match="already belongs"):
                jc.create_job(shop["tid"], {"new_customer": {"name": "Someone Else", "phone": phone},
                                            "new_vehicle": {"registration": "DU00001"},
                                            "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                              "clerk", False)
        assert _count(shop["tid"], "customers") == before_customers
        assert _count(shop["tid"], "job_cards") == before_jobs

    def test_registration_belonging_to_another_customer_is_refused(self, shop):
        with pytest.raises(jc.JobCardError, match="already recorded"):
            jc.create_job(shop["tid"], {"new_customer": {"name": "Other", "phone": "0799999999"},
                                        "new_vehicle": {"registration": "CA123456"},
                                        "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                          "clerk", False)

    def test_same_name_with_a_different_phone_is_allowed(self, shop):
        result = jc.create_job(shop["tid"], {"new_customer": {"name": "Thabo Mokoena", "phone": "0700000001"},
                                             "new_vehicle": {"registration": "TM00001"},
                                             "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                               "clerk", False)
        assert result["id"]


class TestTenantIsolationOfCustomers:
    def test_another_tenants_phone_number_does_not_block_this_tenant(self, shop):
        other, _ = _tenant("beta")
        add_customer(other, "Beta Customer", phone="0831112222")
        result = jc.create_job(shop["tid"], {"new_customer": {"name": "Alpha New", "phone": "0831112222"},
                                             "new_vehicle": {"registration": "AN00001"},
                                             "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                               "clerk", False)
        assert result["id"]

    def test_another_tenants_customer_cannot_be_selected(self, shop):
        other, _ = _tenant("gamma")
        for n in range(5):  # push the foreign id past every customer id in this shop's own database
            add_customer(other, f"Filler {n}", phone=f"08400000{n:02d}")
        foreign = add_customer(other, "Gamma Customer", phone="0841110000")
        assert foreign > _count(shop["tid"], "customers")
        with pytest.raises(jc.JobCardError):
            jc.create_job(shop["tid"], {"customer_id": foreign, "vehicle_id": shop["vehicle"],
                                        "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                          "clerk", False)


# ── Optional mileage ─────────────────────────────────────────────────────────────

class TestOptionalMileage:
    def test_blank_mileage_is_stored_as_null(self, shop):
        job = jc.get_job(shop["tid"], _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"],
                                           mileage_in="")["id"])
        assert job["header"]["mileage_in"] is None

    def test_missing_mileage_is_stored_as_null(self, shop):
        job = jc.get_job(shop["tid"], _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"])["id"])
        assert job["header"]["mileage_in"] is None

    def test_provided_mileage_is_kept(self, shop):
        job = jc.get_job(shop["tid"], _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"],
                                           mileage_in=90000)["id"])
        assert job["header"]["mileage_in"] == 90000


# ── Technicians ──────────────────────────────────────────────────────────────────

class TestTechnicians:
    def test_create_and_list_a_technician(self, shop):
        record = tech.create_technician(shop["tid"], "Sipho", "082 000 1111")
        assert record["name"] == "Sipho" and record["is_active"] == 1
        assert [t["name"] for t in tech.list_technicians(shop["tid"])] == ["Sipho"]

    def test_name_is_required_and_unique(self, shop):
        with pytest.raises(jc.JobCardError):
            tech.create_technician(shop["tid"], "   ")
        tech.create_technician(shop["tid"], "Sipho")
        with pytest.raises(jc.JobCardError, match="already exists"):
            tech.create_technician(shop["tid"], "  sipho ")

    def test_select_a_technician_on_a_job(self, shop):
        record = tech.create_technician(shop["tid"], "Nomsa")
        job = jc.get_job(shop["tid"], _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"],
                                           technician_id=record["id"])["id"])
        assert job["header"]["technician_id"] == record["id"]
        assert job["header"]["technician"] == "Nomsa"

    def test_inactive_technician_cannot_be_newly_assigned(self, shop):
        record = tech.create_technician(shop["tid"], "Old Hand")
        tech.update_technician(shop["tid"], record["id"], "Old Hand", "", False)
        with pytest.raises(jc.JobCardError, match="active technician"):
            _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])

    def test_job_keeps_an_inactive_technician_when_it_is_edited(self, shop):
        record = tech.create_technician(shop["tid"], "Leaving")
        job_id = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])["id"]
        tech.update_technician(shop["tid"], record["id"], "Leaving", "", False)
        jc.update_job(shop["tid"], job_id, {"technician_id": record["id"], "mileage_in": 1000,
                                            "items": [{"item_type": "service", "service_id": shop["service"], "quantity": 1}]},
                      "clerk", False)
        assert jc.get_job(shop["tid"], job_id)["header"]["technician_id"] == record["id"]

    def test_technician_from_another_tenant_is_refused(self, shop):
        other, _ = _tenant("delta")
        foreign = tech.create_technician(other, "Foreign")
        with pytest.raises(jc.JobCardError, match="active technician"):
            _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=foreign["id"])
        assert [t["name"] for t in tech.list_technicians(shop["tid"])] == []

    def test_completed_count_counts_collected_jobs_not_assigned_jobs(self, shop):
        record = tech.create_technician(shop["tid"], "Counter")
        open_job = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])["id"]
        done_job = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])["id"]
        for status in ("in_progress", "ready", "completed"):
            jc.set_status(shop["tid"], done_job, status, "clerk")
        listed = {t["id"]: t for t in tech.list_technicians(shop["tid"])}[record["id"]]
        assert listed["assigned_jobs"] == 2
        assert listed["completed_jobs"] == 1
        assert open_job != done_job

    def test_deactivating_keeps_job_history_and_counts(self, shop):
        record = tech.create_technician(shop["tid"], "Retired")
        job_id = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])["id"]
        tech.update_technician(shop["tid"], record["id"], "Retired", "", False)
        history = tech.technician_history(shop["tid"], record["id"])
        assert history["technician"]["is_active"] == 0
        assert [j["id"] for j in history["jobs"]] == [job_id]
        assert history["jobs"][0]["services"] == ["Wheel Alignment"]

    def test_history_lists_the_job_and_services_performed(self, shop):
        record = tech.create_technician(shop["tid"], "Historian")
        _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"], technician_id=record["id"])
        history = tech.technician_history(shop["tid"], record["id"])
        job = history["jobs"][0]
        assert job["job_number"].startswith("JOB-") and job["registration"] == "CA123456"
        assert job["customer_name"] == "Thabo Mokoena" and job["services"] == ["Wheel Alignment"]

    def test_technicians_are_isolated_by_tenant(self, shop):
        other, _ = _tenant("epsilon")
        tech.create_technician(other, "Only Elsewhere")
        assert tech.list_technicians(shop["tid"]) == []


# ── Services ─────────────────────────────────────────────────────────────────────

class TestServiceCatalogue:
    def test_create_edit_deactivate_and_search(self, shop):
        created = cat.create_service(shop["tid"], "Tyre Rotation", 120, "Four wheels", "Tyres")
        assert created["is_active"] == 1 and created["default_price"] == 120
        edited = cat.update_service(shop["tid"], created["id"], "Tyre Rotation", 150, "Four wheels", "Tyres", True)
        assert edited["default_price"] == 150
        cat.update_service(shop["tid"], created["id"], "Tyre Rotation", 150, "Four wheels", "Tyres", False)
        assert cat.get_service(shop["tid"], created["id"])["is_active"] == 0
        assert [s["name"] for s in cat.list_services(shop["tid"], query="rotat")] == ["Tyre Rotation"]
        assert "Tyre Rotation" not in [s["name"] for s in cat.list_services(shop["tid"], include_inactive=False)]

    @pytest.mark.parametrize("price", [-1, "abc", 10_000_001])
    def test_invalid_prices_are_refused(self, shop, price):
        with pytest.raises(jc.JobCardError):
            cat.create_service(shop["tid"], "Bad Price", price)

    def test_duplicate_service_name_is_refused(self, shop):
        with pytest.raises(jc.JobCardError, match="already exists"):
            cat.create_service(shop["tid"], "wheel alignment", 100)

    def test_services_are_isolated_by_tenant(self, shop):
        other, _ = _tenant("zeta")
        assert all(s["name"] != "Wheel Alignment" for s in cat.list_services(other))

    def test_later_price_change_does_not_alter_an_existing_job(self, shop):
        job_id = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"])["id"]
        cat.update_service(shop["tid"], shop["service"], "Wheel Alignment", 999, "", "", True)
        item = jc.get_job(shop["tid"], job_id)["items"][0]
        assert item["unit_price"] == 350

    def test_service_sale_in_pos_keeps_price_and_does_not_change_stock(self, shop):
        before_stock = _stock(shop["tid"], shop["product"])
        result = complete_sale(shop["tid"], "service-only-sale-1",
                               [{"type": "service", "id": shop["service"], "quantity": 2}], payment_method="cash")
        assert result["success"] is True
        assert _stock(shop["tid"], shop["product"]) == before_stock
        conn = get_conn(shop["tid"])
        try:
            line = conn.execute("SELECT item_type, service_id, product_id, unit_price, quantity FROM invoice_items "
                                "WHERE invoice_id=?", (result["invoice_id"],)).fetchone()
            logs = conn.execute("SELECT COUNT(*) FROM stock_history WHERE product_id=?", (shop["product"],)).fetchone()[0]
        finally:
            conn.close()
        assert line["item_type"] == "service" and line["service_id"] == shop["service"] and line["product_id"] is None
        assert line["unit_price"] == 350 and line["quantity"] == 2
        assert logs == 0


def _stock(tid, product_id):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT current_stock FROM products WHERE id=?", (product_id,)).fetchone()[0]
    finally:
        conn.close()


# ── Job card route and page checks ───────────────────────────────────────────────

@pytest.fixture
def client():
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


def test_job_view_shows_a_dash_when_mileage_is_unknown(client, shop):
    job_id = _job(shop["tid"], shop["customer"], shop["vehicle"], shop["service"])["id"]
    _login(client, shop["username"])
    html = client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert "Mileage in: — km" in html


def test_new_job_form_offers_both_customer_options(client, shop):
    _login(client, shop["username"])
    html = client.get("/jobs/new").get_data(as_text=True)
    assert "Select existing customer" in html and "Add new customer" in html
    assert 'id="technicianSelect"' in html and 'id="newCustomerPhone"' in html


def test_technician_page_and_api_respect_the_tenant(client, shop):
    other, _ = _tenant("eta")
    foreign = tech.create_technician(other, "Foreign Tech")
    _login(client, shop["username"])
    assert client.get(f"/jobs/technicians/{foreign['id']}").status_code == 302
    created = client.post("/jobs/api/technicians", json={"name": "Local Tech", "phone": ""})
    assert created.status_code == 201
    listed = client.get("/jobs/api/technicians").get_json()
    assert [t["name"] for t in listed] == ["Local Tech"]


def test_customer_api_refuses_a_duplicate_phone(client, shop):
    _login(client, shop["username"])
    response = client.post("/jobs/api/customers", json={"name": "Copy", "phone": "0821234567"})
    assert response.status_code == 409
    assert response.get_json()["existing_customer"]["name"] == "Thabo Mokoena"
