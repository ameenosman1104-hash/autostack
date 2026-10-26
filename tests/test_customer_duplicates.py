"""Duplicate phone numbers on the Customers page, the POS customer modal and the edit page."""
import re
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import add_customer, get_conn, init_tenant_db

PASSWORD = "customer-dup-password"


def _tenant(name):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    return tid, username


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def shop(client):
    tid, username = _tenant("dup")
    existing = add_customer(tid, "Thabo Mokoena", phone="082 123 4567")
    other = add_customer(tid, "Lerato Dube", phone="071 555 0199")
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": token},
                           follow_redirects=True)
    meta = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    client.environ_base["HTTP_X_CSRFTOKEN"] = meta.group(1)
    return {"tid": tid, "existing": existing, "other": other}


def _csrf(client, url):
    page = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def _count(tid):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    finally:
        conn.close()


def test_customers_form_opens_the_existing_customer_instead_of_duplicating(client, shop):
    before = _count(shop["tid"])
    token = _csrf(client, "/customers/add")
    response = client.post("/customers/add", data={"name": "Someone New", "phone": "+27 82 123 4567",
                                                   "csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/customers/{shop['existing']}")
    assert _count(shop["tid"]) == before


def test_customers_form_still_adds_a_customer_with_a_new_number(client, shop):
    before = _count(shop["tid"])
    token = _csrf(client, "/customers/add")
    response = client.post("/customers/add", data={"name": "New One", "phone": "0765550000", "csrf_token": token})
    assert response.status_code == 302
    assert _count(shop["tid"]) == before + 1


def test_pos_endpoint_returns_the_existing_customer_for_selection(client, shop):
    response = client.post("/customers/add-json", json={"name": "Copy", "phone": "0821234567"})
    assert response.status_code == 409
    data = response.get_json()
    assert data["success"] is False
    assert data["existing_customer"]["id"] == shop["existing"]
    assert data["existing_customer"]["name"] == "Thabo Mokoena"


def test_editing_a_customer_to_another_customers_number_is_refused(client, shop):
    token = _csrf(client, f"/customers/{shop['other']}/edit")
    response = client.post(f"/customers/{shop['other']}/edit",
                           data={"name": "Lerato Dube", "phone": "0821234567", "csrf_token": token})
    assert response.status_code == 200
    assert "already belongs to Thabo Mokoena" in response.get_data(as_text=True)
    conn = get_conn(shop["tid"])
    try:
        phone = conn.execute("SELECT phone FROM customers WHERE id=?", (shop["other"],)).fetchone()[0]
    finally:
        conn.close()
    assert phone == "071 555 0199"


def test_editing_a_customer_keeps_their_own_number(client, shop):
    token = _csrf(client, f"/customers/{shop['existing']}/edit")
    response = client.post(f"/customers/{shop['existing']}/edit",
                           data={"name": "Thabo M.", "phone": "082 123 4567", "csrf_token": token})
    assert response.status_code == 302


def test_another_tenants_number_does_not_block_this_tenant(client, shop):
    other_tid, _ = _tenant("elsewhere")
    add_customer(other_tid, "Far Away", phone="0799998888")
    token = _csrf(client, "/customers/add")
    response = client.post("/customers/add", data={"name": "Local", "phone": "0799998888", "csrf_token": token})
    assert response.status_code == 302
    assert "/customers/" in response.headers["Location"]
    assert not response.headers["Location"].endswith(f"/customers/{shop['existing']}")
