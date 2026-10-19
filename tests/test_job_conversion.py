"""Phase 4: converting job cards into POS sales invoices.

Uses disposable tenant databases only. Covers payment methods, stock, numbering, retries,
simultaneous requests, price confirmation, statuses, quotation links, rollback and access control.
"""
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from app.services import job_cards as jc
from app.services import job_conversion as jconv
from app.services import quotations as engine
from app.tenant_db import get_conn, save_setting


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    return tmp_path


def seed(tid):
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone, email) VALUES ('Thabo Mokoena', '0821234567', 'thabo@example.com')")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 10, 0)")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T195', 'Eco 195', 'Brand B', '195/65R15', 'Eco', 800, 0, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', '', 150, 1)")
    conn.commit()
    conn.close()
    return jc.upsert_vehicle(tid, 1, "CA 123 456", make="Toyota", model="Corolla", year=2015, actor="seed")["id"]


def ready_job(tid, vehicle_id, items=None, status="ready"):
    created = jc.create_job(tid, {"customer_id": 1, "vehicle_id": vehicle_id, "mileage_in": 90000,
                                  "items": items or [{"item_type": "product", "product_id": 1, "quantity": 2}]},
                            "seed", True)
    jc.set_status(tid, created["id"], "in_progress", "seed")
    jc.set_status(tid, created["id"], "ready", "seed")
    if status == "completed":
        jc.set_status(tid, created["id"], "completed", "seed")
    return created["id"]


def convert(tid, jid, method="cash", actor="cashier", **kwargs):
    kwargs.setdefault("can_convert", True)
    kwargs.setdefault("is_admin", True)
    return jconv.convert_job(tid, jid, actor, method, **kwargs)


def scalar(tid, sql, params=()):
    conn = get_conn(tid)
    value = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return value


def stock(tid, pid=1):
    return scalar(tid, "SELECT current_stock FROM products WHERE id=?", (pid,))


def invoices(tid):
    return scalar(tid, "SELECT COUNT(*) FROM invoices")


def stock_rows(tid):
    return scalar(tid, "SELECT COUNT(*) FROM stock_history")


def job_row(tid, jid):
    conn = get_conn(tid)
    row = dict(conn.execute("SELECT * FROM job_cards WHERE id=?", (jid,)).fetchone())
    conn.close()
    return row


# ── Payment methods and the existing engine ─────────────────────────────────

def test_cash_conversion_deducts_stock_once_and_links_the_invoice(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    result = convert(10, jid, "cash")
    assert result["duplicate"] is False and result["payment_status"] == "paid"
    assert re.fullmatch(r"INV-\d{6}", result["invoice_number"])
    assert stock(10) == 8
    assert stock_rows(10) == 1
    row = job_row(10, jid)
    assert row["invoice_id"] == result["invoice_id"] and row["total"] == 2000.0
    events = [e[0] for e in get_conn(10).execute("SELECT event FROM job_card_events WHERE job_card_id=? ORDER BY id", (jid,)).fetchall()]
    assert events[-1] == "invoiced"


@pytest.mark.parametrize("method,status", [("card", "paid"), ("eft", "paid")])
def test_card_and_eft_need_confirmation_then_record_paid_like_pos(data_dir, method, status):
    vid = seed(10)
    jid = ready_job(10, vid)
    with pytest.raises(jc.JobCardError, match="Confirm that the"):
        convert(10, jid, method, payment_confirmed=False)
    assert invoices(10) == 0 and stock(10) == 10
    result = convert(10, jid, method, payment_confirmed=True)
    assert result["payment_status"] == status
    note = get_conn(10).execute("SELECT notes, payment_method FROM invoices WHERE id=?", (result["invoice_id"],)).fetchone()
    assert note["payment_method"] == method
    assert f"{method.upper()} manual staff confirmation, not bank verification, confirmed by cashier" in note["notes"]


def test_credit_creates_an_unpaid_invoice_and_a_debtor(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    result = convert(10, jid, "credit")
    assert result["payment_status"] == "unpaid" and result["debtor_id"]
    debtor = get_conn(10).execute("SELECT amount_owed, customer_id FROM debtors WHERE id=?", (result["debtor_id"],)).fetchone()
    assert debtor["amount_owed"] == 2000.0 and debtor["customer_id"] == 1
    assert scalar(10, "SELECT debtor_id FROM invoices WHERE id=?", (result["invoice_id"],)) == result["debtor_id"]


def test_invoice_numbers_are_sequential(data_dir):
    vid = seed(10)
    first = convert(10, ready_job(10, vid), "cash")["invoice_number"]
    second = convert(10, ready_job(10, vid), "cash")["invoice_number"]
    assert int(second[4:]) == int(first[4:]) + 1


def test_completed_job_converts_directly(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, status="completed")
    assert convert(10, jid, "cash")["duplicate"] is False


# ── Retries, double clicks and simultaneous requests ────────────────────────

def test_retry_returns_the_same_invoice_and_does_not_deduct_stock_again(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    first = convert(10, jid, "cash")
    stock_after, invoices_after, rows_after = stock(10), invoices(10), stock_rows(10)
    for _ in range(3):
        again = convert(10, jid, "cash")
        assert again["duplicate"] is True and again["invoice_id"] == first["invoice_id"]
    assert stock(10) == stock_after and invoices(10) == invoices_after and stock_rows(10) == rows_after == 1


def test_simultaneous_conversions_create_exactly_one_invoice(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    barrier = threading.Barrier(6)

    def attempt(_):
        barrier.wait(timeout=10)
        return convert(10, jid, "cash")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    fresh = [r for r in results if not r["duplicate"]]
    assert len(fresh) == 1
    assert len({r["invoice_id"] for r in results}) == 1
    assert invoices(10) == 1 and stock(10) == 8 and stock_rows(10) == 1
    assert job_row(10, jid)["invoice_id"] == fresh[0]["invoice_id"]


# ── Stock, prices and rollback ──────────────────────────────────────────────

def test_insufficient_stock_rolls_back_everything(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 2, "quantity": 1}])
    with pytest.raises(jc.JobCardError, match="Insufficient stock"):
        convert(10, jid, "cash")
    assert invoices(10) == 0 and stock_rows(10) == 0
    row = job_row(10, jid)
    assert row["invoice_id"] is None and row["status"] == "ready"
    assert scalar(10, "SELECT COUNT(*) FROM job_card_events WHERE job_card_id=? AND event='invoiced'", (jid,)) == 0


def test_failure_after_invoice_is_created_rolls_back_the_sale(data_dir, monkeypatch):
    vid = seed(10)
    jid = ready_job(10, vid)
    real_event = jc._event

    def fail_on_invoice(conn, job_id, event, actor, detail=""):
        if event == "invoiced":
            raise RuntimeError("injected failure after the invoice was written")
        return real_event(conn, job_id, event, actor, detail)
    monkeypatch.setattr(jc, "_event", fail_on_invoice)
    with pytest.raises(RuntimeError, match="injected failure"):
        convert(10, jid, "cash")
    assert invoices(10) == 0 and stock(10) == 10 and stock_rows(10) == 0
    assert scalar(10, "SELECT COUNT(*) FROM invoice_items") == 0
    assert job_row(10, jid)["invoice_id"] is None


def test_price_change_needs_admin_confirmation_and_is_recorded(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 1, "quantity": 1}])
    conn = get_conn(10)
    conn.execute("UPDATE products SET selling_price=1100 WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(jc.JobCardError, match="Prices have changed"):
        convert(10, jid, "cash", is_admin=False, confirm_price_changes=True)
    with pytest.raises(jc.JobCardError, match="administrator must approve"):
        convert(10, jid, "cash", is_admin=True, confirm_price_changes=False)
    assert invoices(10) == 0 and stock(10) == 10
    result = convert(10, jid, "cash", is_admin=True, confirm_price_changes=True)
    assert result["total"] == 1100.0 and result["approved_changes"] == 1
    row = job_row(10, jid)
    assert row["total"] == 1000.0  # the agreed total is preserved
    assert row["price_confirmed_by"] == "cashier" and row["price_confirmed_at"]
    assert scalar(10, "SELECT unit_price FROM job_card_items WHERE job_card_id=?", (jid,)) == 1000.0
    approval = get_conn(10).execute("SELECT old_price, new_price, approved_by, approved_at, invoice_id "
                                    "FROM job_card_price_approvals WHERE job_card_id=?", (jid,)).fetchone()
    assert approval["old_price"] == 1000.0 and approval["new_price"] == 1100.0
    assert approval["approved_by"] == "cashier" and approval["approved_at"]
    assert approval["invoice_id"] == result["invoice_id"]
    assert scalar(10, "SELECT COUNT(*) FROM job_card_events WHERE job_card_id=? AND event='prices_approved'", (jid,)) == 1


def test_staff_cannot_confirm_price_changes_even_with_the_flag(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 1, "quantity": 1}])
    conn = get_conn(10)
    conn.execute("UPDATE products SET selling_price=900 WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(jc.JobCardError, match="Prices have changed"):
        convert(10, jid, "cash", is_admin=False, confirm_price_changes=True)
    assert invoices(10) == 0


def test_removed_product_is_refused_without_side_effects(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    conn = get_conn(10)
    conn.execute("UPDATE products SET deleted=1 WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(jc.JobCardError, match="no longer available"):
        convert(10, jid, "cash")
    assert invoices(10) == 0


def test_preview_hides_stock_numbers_from_staff(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 2, "quantity": 1}])
    staff = jconv.preview(10, jid, is_admin=False, can_convert=True)
    admin = jconv.preview(10, jid, is_admin=True, can_convert=True)
    assert staff["shortages"] == [{"description": "Eco 195"}]
    assert "needed" in admin["shortages"][0] and admin["shortages"][0]["available"] == 0


# ── Eligibility and invalid input ───────────────────────────────────────────

@pytest.mark.parametrize("state", ["open", "in_progress", "cancelled"])
def test_only_ready_or_completed_jobs_convert(data_dir, state):
    vid = seed(10)
    created = jc.create_job(10, {"customer_id": 1, "vehicle_id": vid,
                                 "items": [{"item_type": "product", "product_id": 1, "quantity": 1}]}, "seed", True)
    if state == "in_progress":
        jc.set_status(10, created["id"], "in_progress", "seed")
    if state == "cancelled":
        jc.set_status(10, created["id"], "cancelled", "seed", can_cancel=True)
    with pytest.raises(jc.JobCardError, match="Only ready or completed"):
        convert(10, created["id"], "cash")
    assert invoices(10) == 0 and stock(10) == 10


def test_invalid_payment_method_refused(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    for bad in ("bitcoin", "", "CASH; DROP TABLE invoices", None):
        with pytest.raises(jc.JobCardError):
            convert(10, jid, bad)
    assert invoices(10) == 0


def test_missing_job_refused(data_dir):
    seed(10)
    with pytest.raises(jc.JobCardError, match="not found"):
        convert(10, 9999, "cash")


def test_permission_is_enforced_by_the_service(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    with pytest.raises(jc.JobCardError, match="not authorised"):
        convert(10, jid, "cash", can_convert=False)
    assert invoices(10) == 0


def test_cross_tenant_conversion_is_refused(data_dir):
    vid = seed(10)
    seed(11)
    jid = ready_job(10, vid)
    with pytest.raises(jc.JobCardError, match="not found"):
        convert(11, jid, "cash")
    assert invoices(10) == 0 and invoices(11) == 0


# ── Invoice state after conversion ──────────────────────────────────────────

def test_invoiced_job_cannot_be_cancelled_or_reopened(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    convert(10, jid, "cash")
    with pytest.raises(jc.JobCardError, match="invoiced job card cannot be cancelled"):
        jc.set_status(10, jid, "cancelled", "owner", can_cancel=True)
    with pytest.raises(jc.JobCardError, match="can only be marked collected"):
        jc.set_status(10, jid, "in_progress", "owner")
    jc.set_status(10, jid, "completed", "owner")  # collection is still allowed
    assert job_row(10, jid)["status"] == "completed"


def test_invoiced_job_cannot_be_edited(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    convert(10, jid, "cash")
    with pytest.raises(jc.JobCardError, match="cannot be edited"):
        jc.update_job(10, jid, {"customer_id": 1, "vehicle_id": vid,
                                "items": [{"item_type": "service", "service_id": 1, "quantity": 1}]}, "owner", True)


# ── Quotation links ─────────────────────────────────────────────────────────

def linked_quote_and_job(tid, vid, quote_customer=1):
    today = date.today()
    quote = engine.create_quotation(tid, {"customer_id": quote_customer, "quote_date": today.isoformat(),
                                          "expiry_date": (today + timedelta(days=20)).isoformat(),
                                          "items": [{"item_type": "product", "product_id": 1, "quantity": 2, "unit_price": 1000}]},
                                    "seed", True)
    engine.set_status(tid, quote["id"], "sent", "seed")
    engine.set_status(tid, quote["id"], "accepted", "seed")
    created = jc.create_job(tid, {"customer_id": 1, "vehicle_id": vid, "quotation_id": quote["id"],
                                  "items": [{"item_type": "product", "product_id": 1, "quantity": 2}]}, "seed", True)
    jc.set_status(tid, created["id"], "in_progress", "seed")
    jc.set_status(tid, created["id"], "ready", "seed")
    return quote["id"], created["id"]


def test_job_conversion_marks_the_linked_quotation_and_blocks_a_second_invoice(data_dir):
    vid = seed(10)
    qid, jid = linked_quote_and_job(10, vid)
    result = convert(10, jid, "cash")
    assert invoices(10) == 1
    quote = get_conn(10).execute("SELECT status, converted_invoice_id FROM quotations WHERE id=?", (qid,)).fetchone()
    assert quote["status"] == "converted" and quote["converted_invoice_id"] == result["invoice_id"]
    again = engine.convert_to_sale(10, qid, "cashier", "cash")
    assert again["duplicate"] is True and again["invoice_id"] == result["invoice_id"]
    assert invoices(10) == 1 and stock(10) == 8


def test_quotation_conversion_is_refused_while_a_job_card_is_linked(data_dir):
    vid = seed(10)
    qid, jid = linked_quote_and_job(10, vid)
    with pytest.raises(engine.QuotationError, match="has a job card"):
        engine.convert_to_sale(10, qid, "cashier", "cash")
    assert invoices(10) == 0 and stock(10) == 10
    assert get_conn(10).execute("SELECT status FROM quotations WHERE id=?", (qid,)).fetchone()[0] == "accepted"


def test_job_conversion_refused_if_the_quotation_was_converted_separately(data_dir):
    vid = seed(10)
    qid, jid = linked_quote_and_job(10, vid)
    conn = get_conn(10)
    conn.execute("UPDATE quotations SET status='converted' WHERE id=?", (qid,))
    conn.commit()
    conn.close()
    with pytest.raises(engine.QuotationError, match="no longer open"):
        convert(10, jid, "cash")
    assert invoices(10) == 0 and job_row(10, jid)["invoice_id"] is None


def test_simultaneous_quotation_and_job_conversions_create_one_invoice(data_dir):
    vid = seed(10)
    qid, jid = linked_quote_and_job(10, vid)
    barrier = threading.Barrier(2)

    def via_job(_):
        barrier.wait(timeout=10)
        try:
            return ("job", convert(10, jid, "cash"))
        except Exception as exc:  # noqa: BLE001 - the outcome is asserted below
            return ("job-error", str(exc))

    def via_quote(_):
        barrier.wait(timeout=10)
        try:
            return ("quote", engine.convert_to_sale(10, qid, "cashier", "cash"))
        except Exception as exc:  # noqa: BLE001
            return ("quote-error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result() for f in [pool.submit(via_job, 0), pool.submit(via_quote, 1)]]
    assert invoices(10) == 1, outcomes
    assert stock(10) == 8 and stock_rows(10) == 1


def test_quotation_without_a_job_still_converts(data_dir):
    seed(10)
    today = date.today()
    quote = engine.create_quotation(10, {"customer_id": 1, "quote_date": today.isoformat(),
                                         "expiry_date": (today + timedelta(days=10)).isoformat(),
                                         "items": [{"item_type": "product", "product_id": 1, "quantity": 1, "unit_price": 1000}]},
                                    "seed", True)
    engine.set_status(10, quote["id"], "sent", "seed")
    engine.set_status(10, quote["id"], "accepted", "seed")
    result = engine.convert_to_sale(10, quote["id"], "cashier", "cash")
    assert result["duplicate"] is False and invoices(10) == 1


# ── Routes: login, CSRF, permissions and tenant isolation ───────────────────

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


def test_convert_routes_require_login(client):
    assert client.post("/jobs/1/convert", json={}).status_code in (302, 401)
    assert client.get("/jobs/1/invoice").status_code == 302


def test_admin_converts_through_the_route_and_repeat_returns_200(app, client):
    add_tenant(10, "owner", admin=True)
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    first = client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"})
    assert first.status_code == 201 and first.get_json()["invoice_number"]
    second = client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"})
    assert second.status_code == 200 and second.get_json()["duplicate"] is True
    assert invoices(10) == 1 and stock(10) == 8


def test_staff_are_refused_by_default_and_allowed_when_admin_enables_it(app, client):
    add_tenant(10, "staffer")
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    refused = client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"})
    assert refused.status_code == 400 and "not authorised" in refused.get_json()["error"]
    assert client.get(f"/jobs/{jid}/invoice").status_code == 302
    assert invoices(10) == 0
    save_setting(10, "job_staff_can_convert", "1")
    allowed = client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"})
    assert allowed.status_code == 201


def test_convert_route_enforces_csrf(app, client):
    add_tenant(10, "owner", admin=True)
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"}).status_code == 400
    assert invoices(10) == 0


def test_other_tenant_cannot_convert_or_see_the_screen(app, client):
    add_tenant(10, "owner-a", admin=True)
    add_tenant(11, "owner-b", admin=True)
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    log_in(client, 11)
    assert client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"}).status_code == 400
    assert client.get(f"/jobs/{jid}/invoice").status_code == 302
    assert invoices(10) == 0


def test_invalid_json_and_unknown_payment_are_rejected(app, client):
    add_tenant(10, "owner", admin=True)
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    assert client.post(f"/jobs/{jid}/convert", json={"payment_method": "gold"}).status_code == 400
    assert client.post(f"/jobs/{jid}/convert", data="not json", content_type="text/plain").status_code == 400
    assert invoices(10) == 0


def test_conversion_screen_and_view_actions_render(app, client):
    add_tenant(10, "owner", admin=True)
    log_in(client, 10)
    vid = seed(10)
    jid = ready_job(10, vid)
    screen = client.get(f"/jobs/{jid}/invoice").get_data(as_text=True)
    assert "Create invoice" in screen and 'name="payment"' in screen and "Credit (debtor)" in screen
    assert "Convert to invoice" in client.get(f"/jobs/{jid}").get_data(as_text=True)
    client.post(f"/jobs/{jid}/convert", json={"payment_method": "cash"})
    view = client.get(f"/jobs/{jid}").get_data(as_text=True)
    assert "Invoiced: INV-" in view and "Convert to invoice" not in view
