"""Phase 4b: agreed prices, admin-approved final prices, price history, manual payment label and migration 28.

Uses disposable databases only.
"""
import os
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db_migrations import LATEST_SCHEMA_VERSION, migrate_tenant_db
from app.services import job_cards as jc
from app.services import job_conversion as jconv
from app.tenant_db import get_conn


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    return tmp_path


def seed(tid):
    conn = get_conn(tid)
    conn.execute("INSERT INTO customers (name, phone) VALUES ('Thabo Mokoena', '0821234567')")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T205', 'Touring 205', 'Brand A', '205/55R16', 'Touring', 1000, 10, 0)")
    conn.execute("INSERT INTO products (code, name, brand, tyre_size, pattern, selling_price, current_stock, deleted) "
                 "VALUES ('T195', 'Eco 195', 'Brand B', '195/65R15', 'Eco', 800, 0, 0)")
    conn.execute("INSERT INTO services (name, description, default_price, is_active) VALUES ('Balancing', '', 150, 1)")
    conn.commit()
    conn.close()
    return jc.upsert_vehicle(tid, 1, "CA 123 456", make="Toyota", model="Corolla", actor="seed")["id"]


def ready_job(tid, vid, items=None):
    created = jc.create_job(tid, {"customer_id": 1, "vehicle_id": vid, "mileage_in": 1000,
                                  "items": items or [{"item_type": "product", "product_id": 1, "quantity": 1}]}, "seed", True)
    jc.set_status(tid, created["id"], "in_progress", "seed")
    jc.set_status(tid, created["id"], "ready", "seed")
    return created["id"]


def set_price(tid, pid, price):
    conn = get_conn(tid)
    conn.execute("UPDATE products SET selling_price=? WHERE id=?", (price, pid))
    conn.commit()
    conn.close()


def count(tid, sql, params=()):
    conn = get_conn(tid)
    value = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return value


def convert(tid, jid, method="cash", **kwargs):
    kwargs.setdefault("can_convert", True)
    kwargs.setdefault("is_admin", True)
    return jconv.convert_job(tid, jid, "owner", method, **kwargs)


# ── Agreed prices are preserved; approved prices are used on the invoice ────

def test_agreed_prices_are_preserved_and_the_invoice_uses_the_approved_price(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    result = convert(10, jid, confirm_price_changes=True)
    assert result["total"] == 1100.0
    assert count(10, "SELECT unit_price FROM job_card_items WHERE job_card_id=?", (jid,)) == 1000.0
    assert count(10, "SELECT total FROM job_cards WHERE id=?", (jid,)) == 1000.0
    assert count(10, "SELECT unit_price FROM invoice_items WHERE invoice_id=?", (result["invoice_id"],)) == 1100.0
    assert count(10, "SELECT total FROM invoices WHERE id=?", (result["invoice_id"],)) == 1100.0


def test_approval_records_old_new_price_user_date_and_invoice(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    result = convert(10, jid, confirm_price_changes=True)
    rows = get_conn(10).execute("SELECT item_position, description, old_price, new_price, approved_by, approved_at, invoice_id "
                                "FROM job_card_price_approvals WHERE job_card_id=?", (jid,)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["item_position"] == 1 and row["description"] == "Touring 205"
    assert row["old_price"] == 1000.0 and row["new_price"] == 1100.0
    assert row["approved_by"] == "owner" and row["approved_at"]
    assert row["invoice_id"] == result["invoice_id"]
    detail = jc.get_job(10, jid)
    assert detail["approvals"][0]["new_price"] == 1100.0


def test_price_drop_is_recorded_too(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 900)
    result = convert(10, jid, confirm_price_changes=True)
    assert result["total"] == 900.0
    assert count(10, "SELECT old_price FROM job_card_price_approvals WHERE job_card_id=?", (jid,)) == 1000.0


def test_items_without_a_change_are_not_approved(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 1, "quantity": 1},
                                    {"item_type": "service", "service_id": 1, "quantity": 2}])
    set_price(10, 1, 1100)
    convert(10, jid, confirm_price_changes=True)
    rows = get_conn(10).execute("SELECT item_position FROM job_card_price_approvals WHERE job_card_id=?", (jid,)).fetchall()
    assert [r[0] for r in rows] == [1]


def test_catalogue_changes_after_invoicing_do_not_rewrite_history(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    convert(10, jid, confirm_price_changes=True)
    set_price(10, 1, 1300)
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals WHERE job_card_id=?", (jid,)) == 1
    assert count(10, "SELECT new_price FROM job_card_price_approvals WHERE job_card_id=?", (jid,)) == 1100.0
    assert count(10, "SELECT unit_price FROM job_card_items WHERE job_card_id=?", (jid,)) == 1000.0


# ── Retries and failed conversions never alter price history ────────────────

def test_retries_after_an_approved_conversion_add_no_history(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    first = convert(10, jid, confirm_price_changes=True)
    for _ in range(3):
        again = convert(10, jid, confirm_price_changes=True)
        assert again["duplicate"] is True and again["invoice_id"] == first["invoice_id"]
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals WHERE job_card_id=?", (jid,)) == 1
    assert count(10, "SELECT COUNT(*) FROM invoices") == 1


def test_simultaneous_approved_conversions_record_one_approval(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    barrier = threading.Barrier(5)

    def attempt(_):
        barrier.wait(timeout=10)
        return convert(10, jid, confirm_price_changes=True)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals WHERE job_card_id=?", (jid,)) == 1
    assert count(10, "SELECT COUNT(*) FROM invoices") == 1


def test_failed_conversion_after_a_price_change_leaves_no_history(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid, items=[{"item_type": "product", "product_id": 1, "quantity": 1},
                                    {"item_type": "product", "product_id": 2, "quantity": 1}])
    set_price(10, 1, 1100)
    with pytest.raises(jc.JobCardError, match="Insufficient stock"):
        convert(10, jid, confirm_price_changes=True)
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals") == 0
    assert count(10, "SELECT unit_price FROM job_card_items WHERE job_card_id=? AND position=1", (jid,)) == 1000.0
    assert count(10, "SELECT COUNT(*) FROM invoices") == 0


def test_approval_rows_roll_back_when_a_later_step_fails(data_dir, monkeypatch):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    real_event = jc._event

    def fail_on_invoice(conn, job_id, event, actor, detail=""):
        if event == "invoiced":
            raise RuntimeError("injected failure after approval rows were written")
        return real_event(conn, job_id, event, actor, detail)
    monkeypatch.setattr(jc, "_event", fail_on_invoice)
    with pytest.raises(RuntimeError, match="injected failure"):
        convert(10, jid, confirm_price_changes=True)
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals") == 0
    assert count(10, "SELECT COUNT(*) FROM invoices") == 0 and count(10, "SELECT current_stock FROM products WHERE id=1") == 10
    assert count(10, "SELECT invoice_id FROM job_cards WHERE id=?", (jid,)) is None


def test_refused_staff_approval_records_nothing(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    with pytest.raises(jc.JobCardError, match="Prices have changed"):
        convert(10, jid, is_admin=False, confirm_price_changes=True)
    assert count(10, "SELECT COUNT(*) FROM job_card_price_approvals") == 0


def test_conversion_screen_renders_with_a_price_change(tmp_path, monkeypatch):
    """Regression: the screen once failed to render when a price had changed."""
    import sys
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSTACK_ENV", "test")
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
        from app.main_db import get_conn as main_conn
        mc = main_conn()
        mc.execute("INSERT INTO tenants (id, business_name, username, password_hash, is_admin) VALUES (10,'Shop','owner','x',1)")
        mc.commit()
        mc.close()
        vid = seed(10)
        jid = ready_job(10, vid)
        set_price(10, 1, 1100)
        client = application.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "10"
            session["_fresh"] = True
        html = client.get(f"/jobs/{jid}/invoice").get_data(as_text=True)
        assert "Prices have changed" in html and "was R1000.00, now R1100.00" in html
        assert "Invoice-level discount" in html and "Manual confirmation, not bank verification" in html
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_preview_shows_agreed_approved_and_final_prices(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    set_price(10, 1, 1100)
    line = jconv.preview(10, jid, is_admin=True, can_convert=True)["lines"][0]
    assert line["agreed_price"] == 1000.0 and line["approved_price"] == 1000.0 and line["final_price"] == 1100.0
    assert line["changed"] is True


# ── Manual payment confirmation is labelled as such ─────────────────────────

@pytest.mark.parametrize("method", ["card", "eft"])
def test_card_and_eft_notes_say_manual_not_bank_verified(data_dir, method):
    vid = seed(10)
    jid = ready_job(10, vid)
    result = convert(10, jid, method, payment_confirmed=True)
    note = count(10, "SELECT notes FROM invoices WHERE id=?", (result["invoice_id"],))
    assert "manual staff confirmation, not bank verification" in note
    assert f"{method.upper()}" in note and "owner" in note


def test_card_confirmation_message_is_explicit(data_dir):
    vid = seed(10)
    jid = ready_job(10, vid)
    with pytest.raises(jc.JobCardError, match="not bank verification"):
        convert(10, jid, "card", payment_confirmed=False)


# ── Migration 28: additive, backed up, reversible ───────────────────────────

def test_migration_28_adds_the_history_table_and_keeps_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "40.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(40, str(path))
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE job_card_price_approvals")
    conn.execute("DELETE FROM schema_migrations WHERE version>=28")
    conn.execute("INSERT INTO customers (name) VALUES ('Keep me')")
    conn.commit()
    conn.close()
    migrate_tenant_db(40, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == LATEST_SCHEMA_VERSION == 31
    assert conn.execute("SELECT COUNT(*) FROM customers WHERE name='Keep me'").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_migration_28_creates_a_backup_and_rollback_restores_the_previous_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "41.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(41, str(path))
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE job_card_price_approvals")
    conn.execute("DELETE FROM schema_migrations WHERE version>=28")
    conn.execute("INSERT INTO customers (name) VALUES ('Before upgrade')")
    conn.commit()
    conn.close()
    migrate_tenant_db(41, str(path))
    backups = sorted((path.parent / ".backups").glob("*.db"))
    assert backups, "a verified backup must exist before the upgrade"
    assert sqlite3.connect(backups[-1]).execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Rollback drill: restore the pre-upgrade backup into a scratch copy and check its state.
    rehearsal = tmp_path / "rehearsal.db"
    shutil.copy2(backups[-1], rehearsal)
    conn = sqlite3.connect(rehearsal)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 27
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "job_card_price_approvals" not in tables
    assert conn.execute("SELECT COUNT(*) FROM customers WHERE name='Before upgrade'").fetchone()[0] == 1
    conn.close()


def test_migration_28_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "42.db"
    path.parent.mkdir(parents=True)
    migrate_tenant_db(42, str(path))
    migrate_tenant_db(42, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=28").fetchone()[0] == 1
    conn.close()
