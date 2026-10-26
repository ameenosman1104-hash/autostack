"""Job cards and vehicles, Phase 1: schema, validation, numbering, workflow, audit and isolation.

No stock is moved by job cards in Phase 1. Tests prove this, and prove that failed writes leave no trace.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from app.db_migrations import LATEST_SCHEMA_VERSION, JOB_CARD_SCHEMA_STATEMENTS, migrate_tenant_db
from app.services import job_cards as jc
from app.services import quotations as engine
from app.tenant_db import get_conn


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    return tmp_path


def seed(tid):
    """Create a tenant with two customers, two products, two services and one accepted quotation."""
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone, email) VALUES ('Thabo Mokoena', '0821234567', 'thabo@example.com')")
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Lerato Dlamini', '0839990000')")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 5, 0)")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T195', 'Empty 195', 'Brand B', '195/65R15', 'Eco', 800, 0, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', 'Four wheels', 150, 1)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Alignment', '', 450, 1)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Retired', '', 10, 0)")
    conn.commit()
    conn.close()


def vehicle(tid, customer_id=1, reg="CA 123 456"):
    return jc.upsert_vehicle(tid, customer_id, reg, make="Toyota", model="Corolla", year=2015, actor="tester")


def line(kind, item_id, qty=1, price=None, disc=0):
    key = "product_id" if kind == "product" else "service_id"
    raw = {"item_type": kind, key: item_id, "quantity": qty, "discount_pct": disc}
    if price is not None:
        raw["unit_price"] = price
    return raw


def job_data(vehicle_id, customer_id=1, items=None, **extra):
    data = {"customer_id": customer_id, "vehicle_id": vehicle_id, "mileage_in": 84000,
            "items": items or [line("service", 1)]}
    data.update(extra)
    return data


def count(tid, table):
    conn = get_conn(tid)
    value = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return value


def stock(tid, product_id):
    conn = get_conn(tid)
    value = conn.execute("SELECT current_stock FROM products WHERE id=?", (product_id,)).fetchone()[0]
    conn.close()
    return value


# ── Schema and migration ────────────────────────────────────────────────────

def test_migration_26_creates_job_card_tables_and_version(data_dir):
    conn = get_conn(10)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vehicles", "job_cards", "job_card_items", "job_card_events", "job_card_sequence"} <= tables
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == LATEST_SCHEMA_VERSION == 29
    conn.close()


def test_migration_26_on_populated_schema_25_keeps_existing_data(data_dir):
    """Simulate a production tenant at schema 25 with real rows, then upgrade it."""
    path = data_dir / "tenants" / "20.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(20, str(path))
    conn = sqlite3.connect(path)
    for statement in ("DROP TABLE job_card_events", "DROP TABLE job_card_items", "DROP TABLE job_cards",
                      "DROP TABLE job_card_sequence", "DROP TABLE vehicles",
                      "DELETE FROM schema_migrations WHERE version>=26"):
        conn.execute(statement)
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Existing Customer', '0110000000')")
    conn.execute("INSERT INTO products (code, name, selling_price, current_stock, deleted) VALUES ('KEEP', 'Keep me', 50, 9, 0)")
    conn.commit()
    before = conn.execute("SELECT id, name, phone FROM customers ORDER BY id").fetchall()
    conn.close()

    migrate_tenant_db(20, str(path))

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 29
    assert conn.execute("SELECT id, name, phone FROM customers ORDER BY id").fetchall() == before
    assert conn.execute("SELECT current_stock FROM products WHERE code='KEEP'").fetchone()[0] == 9
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()


def test_migration_26_backs_up_before_changing_a_populated_database(data_dir):
    path = data_dir / "tenants" / "21.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(21, str(path))
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE job_card_events")
    conn.execute("DROP TABLE job_card_items")
    conn.execute("DROP TABLE job_cards")
    conn.execute("DROP TABLE job_card_sequence")
    conn.execute("DROP TABLE vehicles")
    conn.execute("DELETE FROM schema_migrations WHERE version>=26")
    conn.execute("INSERT INTO customers (name) VALUES ('Has data')")
    conn.commit()
    conn.close()
    migrate_tenant_db(21, str(path))
    backups = list((path.parent / ".backups").glob("*.db"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1


def test_normal_connections_create_no_backups(data_dir):
    get_conn(22).close()
    for _ in range(4):
        get_conn(22).close()
    assert not (data_dir / "tenants" / ".backups").exists()


def test_statement_list_only_creates_objects_and_seeds_sequence():
    for statement in JOB_CARD_SCHEMA_STATEMENTS:
        assert statement.strip().split()[0].upper() in ("CREATE", "INSERT")


# ── Registration and vehicles ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [("ca 123-456", "CA123456"), ("  cy41vp ", "CY41VP"), ("ab", "AB")])
def test_registration_normalised(raw, expected):
    assert jc.normalise_registration(raw) == expected


@pytest.mark.parametrize("raw", ["", "A", "TOO-LONG-REGISTRATION-123", "CA*123", "CA;DROP"])
def test_invalid_registration_rejected(raw):
    with pytest.raises(jc.JobCardError):
        jc.normalise_registration(raw)


def test_vehicle_created_once_and_updated_for_same_customer(data_dir):
    seed(10)
    first = vehicle(10)
    again = jc.upsert_vehicle(10, 1, "CA123456", make="", model="Corolla GR", actor="tester")
    assert again["id"] == first["id"]
    assert again["make"] == "Toyota" and again["model"] == "Corolla GR"
    assert count(10, "vehicles") == 1


def test_registration_belonging_to_another_customer_is_refused(data_dir):
    seed(10)
    vehicle(10, customer_id=1)
    with pytest.raises(jc.JobCardError, match="another customer"):
        jc.upsert_vehicle(10, 2, "CA123456")


def test_vehicle_requires_existing_customer_and_valid_year(data_dir):
    seed(10)
    with pytest.raises(jc.JobCardError, match="Customer not found"):
        jc.upsert_vehicle(10, 999, "XY1")
    with pytest.raises(jc.JobCardError, match="Year"):
        jc.upsert_vehicle(10, 1, "XY2", year=1800)


def test_vehicle_history_lists_jobs_for_that_vehicle_only(data_dir):
    seed(10)
    v1, v2 = vehicle(10, reg="AAA111"), vehicle(10, reg="BBB222")
    a = jc.create_job(10, job_data(v1["id"]), "tester", False)
    jc.create_job(10, job_data(v2["id"], customer_id=1), "tester", False)
    history = jc.vehicle_history(10, v1["id"])
    assert [h["job_number"] for h in history] == [a["job_number"]]


# ── Creating and numbering jobs ─────────────────────────────────────────────

def test_job_numbers_are_sequential_and_unique(data_dir):
    seed(10)
    v = vehicle(10)
    numbers = [jc.create_job(10, job_data(v["id"]), "tester", False)["job_number"] for _ in range(3)]
    assert numbers == ["JOB-000001", "JOB-000002", "JOB-000003"]


def test_creating_a_job_does_not_move_stock_or_write_stock_history(data_dir):
    seed(10)
    v = vehicle(10)
    before_stock, before_history = stock(10, 1), count(10, "stock_history")
    result = jc.create_job(10, job_data(v["id"], items=[line("product", 1, qty=2)]), "tester", False)
    assert stock(10, 1) == before_stock
    assert count(10, "stock_history") == before_history
    assert count(10, "sales") == 0 and count(10, "invoices") == 0
    assert result["job_number"]


def test_totals_and_discounts_are_calculated(data_dir):
    seed(10)
    v = vehicle(10)
    result = jc.create_job(10, job_data(v["id"], items=[line("product", 1, qty=2, disc=10), line("service", 1, qty=1)]),
                           "tester", False)
    detail = jc.get_job(10, result["id"])
    assert result["total"] == 2000 * 0.9 + 150
    assert detail["header"]["discount_total"] == 200.0
    assert detail["header"]["subtotal"] == 2150.0


def test_short_stock_warns_but_does_not_block(data_dir):
    seed(10)
    v = vehicle(10)
    result = jc.create_job(10, job_data(v["id"], items=[line("product", 2, qty=3)]), "tester", False)
    assert result["warnings"] == [{"product_id": 2, "description": "Empty 195", "needed": 3.0, "available": 0.0}]
    assert jc.get_job(10, result["id"])["warnings"] == result["warnings"]


def test_staff_cannot_change_agreed_price_admin_can(data_dir):
    seed(10)
    v = vehicle(10)
    with pytest.raises(jc.JobCardError, match="not authorised"):
        jc.create_job(10, job_data(v["id"], items=[line("service", 1, price=99)]), "staff", False)
    result = jc.create_job(10, job_data(v["id"], items=[line("service", 1, price=99)]), "owner", True)
    assert jc.get_job(10, result["id"])["items"][0]["unit_price"] == 99.0


def test_discount_cap_applies_to_staff(data_dir):
    seed(10)
    v = vehicle(10)
    with pytest.raises(jc.JobCardError, match="exceeds your limit"):
        jc.create_job(10, job_data(v["id"], items=[line("service", 1, disc=25)]), "staff", False)


def test_inactive_service_and_missing_product_refused(data_dir):
    seed(10)
    v = vehicle(10)
    with pytest.raises(jc.JobCardError, match="Service not found"):
        jc.create_job(10, job_data(v["id"], items=[line("service", 3)]), "tester", False)
    with pytest.raises(jc.JobCardError, match="Product not found"):
        jc.create_job(10, job_data(v["id"], items=[line("product", 999)]), "tester", False)


def test_vehicle_must_belong_to_the_customer(data_dir):
    seed(10)
    v = vehicle(10, customer_id=1)
    with pytest.raises(jc.JobCardError, match="different customer"):
        jc.create_job(10, job_data(v["id"], customer_id=2), "tester", False)


def test_failed_create_rolls_back_everything(data_dir):
    seed(10)
    v = vehicle(10)
    with pytest.raises(jc.JobCardError):
        jc.create_job(10, job_data(v["id"], items=[line("service", 1), line("service", 999)]), "tester", False)
    assert count(10, "job_cards") == 0 and count(10, "job_card_items") == 0 and count(10, "job_card_events") == 0
    # The failed attempt must not consume a job number.
    assert jc.create_job(10, job_data(v["id"]), "tester", False)["job_number"] == "JOB-000001"


def test_validation_rejects_bad_mileage_dates_and_long_text(data_dir):
    seed(10)
    v = vehicle(10)
    with pytest.raises(jc.JobCardError, match="Mileage in"):
        jc.create_job(10, job_data(v["id"], mileage_in=-5), "tester", False)
    with pytest.raises(jc.JobCardError, match="Promised date"):
        jc.create_job(10, job_data(v["id"], promised_date="31/12/2026"), "tester", False)
    with pytest.raises(jc.JobCardError, match="too long"):
        jc.create_job(10, job_data(v["id"], notes="x" * 3000), "tester", False)


# ── Quotation links ─────────────────────────────────────────────────────────

def accepted_quote(tid, customer_id=1):
    today = date.today()
    created = engine.create_quotation(tid, {"customer_id": customer_id, "quote_date": today.isoformat(),
                                            "expiry_date": (today + timedelta(days=20)).isoformat(),
                                            "items": [{"item_type": "service", "service_id": 1, "quantity": 1, "unit_price": 150}]},
                                      "tester", True)
    engine.set_status(tid, created["id"], "sent", "tester")
    engine.set_status(tid, created["id"], "accepted", "tester")
    return created["id"]


def test_accepted_quotation_creates_one_linked_job(data_dir):
    seed(10)
    v = vehicle(10)
    qid = accepted_quote(10)
    result = jc.create_job(10, job_data(v["id"], quotation_id=qid), "tester", False)
    assert jc.get_job(10, result["id"])["header"]["quotation_id"] == qid
    with pytest.raises(jc.JobCardError, match="already has a job card"):
        jc.create_job(10, job_data(v["id"], quotation_id=qid), "tester", False)


def test_database_enforces_one_job_per_quotation(data_dir):
    seed(10)
    v = vehicle(10)
    qid = accepted_quote(10)
    jc.create_job(10, job_data(v["id"], quotation_id=qid), "tester", False)
    conn = get_conn(10)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO job_cards (job_number, customer_id, vehicle_id, customer_name, quotation_id) "
                     "VALUES ('JOB-999999', 1, ?, 'x', ?)", (v["id"], qid))
    conn.close()


def test_only_accepted_quotations_create_jobs(data_dir):
    seed(10)
    v = vehicle(10)
    today = date.today()
    draft = engine.create_quotation(10, {"customer_id": 1, "quote_date": today.isoformat(),
                                         "expiry_date": (today + timedelta(days=5)).isoformat(),
                                         "items": [{"item_type": "service", "service_id": 1, "quantity": 1, "unit_price": 150}]},
                                    "tester", True)
    with pytest.raises(jc.JobCardError, match="Only accepted"):
        jc.create_job(10, job_data(v["id"], quotation_id=draft["id"]), "tester", False)


def test_quotation_for_another_customer_is_refused(data_dir):
    seed(10)
    v = vehicle(10, customer_id=2)
    qid = accepted_quote(10, customer_id=1)
    with pytest.raises(jc.JobCardError, match="different customer"):
        jc.create_job(10, job_data(v["id"], customer_id=2, quotation_id=qid), "tester", False)


# ── Editing, status workflow and audit ──────────────────────────────────────

def test_edit_allowed_while_open_or_in_progress_only(data_dir):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    jc.update_job(10, job["id"], job_data(v["id"], items=[line("service", 2)]), "tester", False)
    assert jc.get_job(10, job["id"])["header"]["total"] == 450.0
    jc.set_status(10, job["id"], "in_progress", "tester")
    jc.update_job(10, job["id"], job_data(v["id"], items=[line("service", 1)]), "tester", False)
    jc.set_status(10, job["id"], "ready", "tester")
    with pytest.raises(jc.JobCardError, match="cannot be edited"):
        jc.update_job(10, job["id"], job_data(v["id"]), "tester", False)


def test_valid_workflow_and_completion_details(data_dir):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    jc.set_status(10, job["id"], "in_progress", "tester")
    jc.set_technician(10, job["id"], "Sipho", "tester")
    jc.set_status(10, job["id"], "ready", "tester")
    jc.set_status(10, job["id"], "completed", "tester", mileage_out=84250)
    header = jc.get_job(10, job["id"])["header"]
    assert header["status"] == "completed"
    assert header["completed_at"] is not None and header["mileage_out"] == 84250
    assert header["technician"] == "Sipho"


@pytest.mark.parametrize("path", [["completed"], ["ready"], ["in_progress", "completed"], ["cancelled", "open"]])
def test_invalid_transitions_refused(data_dir, path):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    if path[0] == "cancelled":
        jc.set_status(10, job["id"], "cancelled", "owner", can_cancel=True)
    with pytest.raises(jc.JobCardError, match="Cannot change status"):
        for step in path:
            jc.set_status(10, job["id"], step, "tester", can_cancel=True)


def test_cancel_requires_permission_and_is_refused_once_invoiced(data_dir):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    with pytest.raises(jc.JobCardError, match="not authorised to cancel"):
        jc.set_status(10, job["id"], "cancelled", "staff", can_cancel=False)
    conn = get_conn(10)
    conn.execute("INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, total, payment_method, payment_status) "
                 "VALUES ('INV-TEST-1', 'key-1', '2026-10-18', 'completed', 150, 'cash', 'paid')")
    invoice_id = conn.execute("SELECT id FROM invoices WHERE invoice_number='INV-TEST-1'").fetchone()[0]
    conn.execute("UPDATE job_cards SET invoice_id=? WHERE id=?", (invoice_id, job["id"]))
    conn.commit()
    conn.close()
    with pytest.raises(jc.JobCardError, match="invoiced job card cannot be cancelled"):
        jc.set_status(10, job["id"], "cancelled", "owner", can_cancel=True)


def test_one_invoice_can_link_to_only_one_job(data_dir):
    seed(10)
    v = vehicle(10)
    a = jc.create_job(10, job_data(v["id"]), "tester", False)
    b = jc.create_job(10, job_data(v["id"]), "tester", False)
    conn = get_conn(10)
    conn.execute("INSERT INTO invoices (invoice_number, idempotency_key, sale_date, status, total, payment_method, payment_status) "
                 "VALUES ('INV-TEST-2', 'key-2', '2026-10-18', 'completed', 150, 'cash', 'paid')")
    invoice_id = conn.execute("SELECT id FROM invoices WHERE invoice_number='INV-TEST-2'").fetchone()[0]
    conn.execute("UPDATE job_cards SET invoice_id=? WHERE id=?", (invoice_id, a["id"]))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE job_cards SET invoice_id=? WHERE id=?", (invoice_id, b["id"]))
    conn.close()


def test_audit_events_recorded_in_order(data_dir):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    jc.set_status(10, job["id"], "in_progress", "mechanic")
    events = [e["event"] for e in reversed(jc.get_job(10, job["id"])["events"])]
    assert events == ["created", "status:in_progress"]
    assert jc.get_job(10, job["id"])["events"][0]["actor"] == "mechanic"


def test_price_changes_detected_after_catalogue_update(data_dir):
    seed(10)
    v = vehicle(10)
    job = jc.create_job(10, job_data(v["id"], items=[line("product", 1, qty=1)]), "tester", False)
    assert jc.price_changes(10, job["id"]) == []
    conn = get_conn(10)
    conn.execute("UPDATE products SET selling_price=1100 WHERE id=1")
    conn.commit()
    conn.close()
    changes = jc.price_changes(10, job["id"])
    assert changes == [{"position": 1, "description": "Touring 205", "agreed": 1000.0, "current": 1100.0}]
    assert jc.get_job(10, job["id"])["items"][0]["unit_price"] == 1000.0


# ── Tenant isolation ────────────────────────────────────────────────────────

def test_tenants_never_see_each_others_jobs_or_vehicles(data_dir):
    seed(10)
    seed(11)
    v = vehicle(10, reg="TENANT1")
    job = jc.create_job(10, job_data(v["id"]), "tester", False)
    assert jc.list_jobs(11) == []
    assert jc.get_job(11, job["id"]) is None
    assert jc.list_vehicles(11) == []
    assert jc.vehicle_history(11, v["id"]) == []
    with pytest.raises(jc.JobCardError):
        jc.set_status(11, job["id"], "in_progress", "tester")
    # The same registration may exist independently in another shop.
    assert jc.upsert_vehicle(11, 1, "TENANT1")["registration"] == "TENANT1"


def test_list_jobs_filters_by_status_and_search(data_dir):
    seed(10)
    v = vehicle(10, reg="SEARCH1")
    a = jc.create_job(10, job_data(v["id"]), "tester", False)
    jc.create_job(10, job_data(v["id"]), "tester", False)
    jc.set_status(10, a["id"], "in_progress", "tester")
    assert [j["job_number"] for j in jc.list_jobs(10, status="in_progress")] == [a["job_number"]]
    assert len(jc.list_jobs(10, search="SEARCH1")) == 2
    assert jc.list_jobs(10, search="Thabo")[0]["registration"] == "SEARCH1"
