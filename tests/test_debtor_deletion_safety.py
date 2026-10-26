"""Debtor deletion never deletes payment history. Every deletion path is covered, with and without payments."""
import re
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import (DebtorDeletionBlocked, add_debtor, add_payment, delete_all_debtors, delete_debtor,
                           delete_debtors_by_ids, get_conn, get_debtor, init_tenant_db)

PASSWORD = "debtor-delete-password"


@pytest.fixture
def shop():
    username = "delete_" + uuid4().hex[:10]
    tid, err = create_tenant("Delete Shop", username, "delete@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    plain = add_debtor(tid, "No Payments", "", "a@example.com", 300, "2026-09-01", "email", 28, "")
    paid = add_debtor(tid, "Has Payments", "", "b@example.com", 500, "2026-09-01", "email", 28, "")
    add_payment(tid, paid, 200, payment_date="2026-09-05", payment_method="cash")
    return {"tid": tid, "username": username, "plain": plain, "paid": paid}


def _payments(tid, debtor_id):
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT COUNT(*) FROM payment_history WHERE debtor_id=?", (debtor_id,)).fetchone()[0]
    finally:
        conn.close()


def test_delete_a_debtor_without_payments_works(shop):
    delete_debtor(shop["tid"], shop["plain"])
    assert get_debtor(shop["tid"], shop["plain"]) is None


def test_delete_a_debtor_with_payments_is_refused_and_keeps_the_history(shop):
    with pytest.raises(DebtorDeletionBlocked, match="recorded payments"):
        delete_debtor(shop["tid"], shop["paid"])
    assert get_debtor(shop["tid"], shop["paid"]) is not None
    assert _payments(shop["tid"], shop["paid"]) == 1


def test_delete_all_keeps_debtors_with_payments(shop):
    deleted, kept = delete_all_debtors(shop["tid"])
    assert (deleted, kept) == (1, 1)
    assert get_debtor(shop["tid"], shop["plain"]) is None
    assert get_debtor(shop["tid"], shop["paid"]) is not None
    assert _payments(shop["tid"], shop["paid"]) == 1


def test_selected_delete_is_all_or_nothing(shop):
    with pytest.raises(DebtorDeletionBlocked):
        delete_debtors_by_ids(shop["tid"], [shop["plain"], shop["paid"]])
    assert get_debtor(shop["tid"], shop["plain"]) is not None, "nothing may be deleted when one selected debtor is paid"
    assert get_debtor(shop["tid"], shop["paid"]) is not None


def test_selected_delete_without_payments_works(shop):
    assert delete_debtors_by_ids(shop["tid"], [shop["plain"]]) == 1
    assert get_debtor(shop["tid"], shop["plain"]) is None


def test_deletion_is_scoped_to_the_tenant(shop):
    username = "other_" + uuid4().hex[:10]
    other, _ = create_tenant("Other Shop", username, "o@example.com", PASSWORD)
    init_tenant_db(other)
    mine = add_debtor(other, "Their Debtor", "", "t@example.com", 100, "2026-09-01", "email", 28, "")
    delete_all_debtors(shop["tid"])
    assert get_debtor(other, mine) is not None


def test_delete_route_shows_the_refusal_and_keeps_the_debtor(shop):
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": shop["username"], "password": PASSWORD, "csrf_token": token},
                           follow_redirects=True)
    meta = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    client.environ_base["HTTP_X_CSRFTOKEN"] = meta.group(1)
    response = client.post(f"/debtors/{shop['paid']}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "recorded payments" in response.get_data(as_text=True)
    assert get_debtor(shop["tid"], shop["paid"]) is not None
