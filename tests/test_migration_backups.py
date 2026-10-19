"""Backup regression tests use disposable databases only."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app import db_migrations as migrations
from app.tenant_db import get_conn


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    path = tmp_path / "tenants" / "1.db"
    migrations.migrate_tenant_db(1, str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO products(code, name, current_stock) VALUES ('KEEP', 'Keep me', 7)")
    return path


def pending(path):
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS quotation_events")
        conn.execute("DROP TABLE IF EXISTS quotation_items")
        conn.execute("DROP TABLE IF EXISTS quotations")
        conn.execute("DROP TABLE supplier_invoice_confirmations")
        conn.execute("DELETE FROM schema_migrations WHERE version>=24")


def backups(path):
    return list((path.parent / ".backups").glob("*.db"))


def assert_record(path):
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name, current_stock FROM products WHERE code='KEEP'").fetchone() == ("Keep me", 7)


def test_normal_connections_do_not_backup(database, monkeypatch):
    def forbidden(*args):
        pytest.fail("Current schema must not create a backup")
    monkeypatch.setattr(migrations, "backup_database", forbidden)
    for _ in range(5):
        conn = get_conn(1)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()
    assert backups(database) == []
    assert_record(database)


def test_pending_migration_backs_up_before_changes(database):
    pending(database)
    conn = get_conn(1)
    assert migrations.get_schema_version(conn) == migrations.LATEST_SCHEMA_VERSION
    conn.close()
    saved, = backups(database)
    with sqlite3.connect(saved) as conn:
        assert migrations.get_schema_version(conn) == 23
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='supplier_invoice_confirmations'").fetchone() is None
    assert_record(saved)
    assert_record(database)
    get_conn(1).close()
    assert backups(database) == [saved]


def test_failed_backup_prevents_changes(database, monkeypatch):
    pending(database)
    def fail(*args):
        raise OSError("unavailable")
    monkeypatch.setattr(migrations, "backup_database", fail)
    with pytest.raises(RuntimeError, match="verified backup"):
        get_conn(1)
    with sqlite3.connect(database) as conn:
        assert migrations.get_schema_version(conn) == 23
    assert_record(database)


def test_failed_migration_rolls_back_and_retry_backs_up(database, monkeypatch):
    pending(database)
    real_mark = migrations.mark_migration_applied
    def fail(conn, version, name):
        if version == 24:
            raise RuntimeError("injected failure")
        return real_mark(conn, version, name)
    monkeypatch.setattr("app.services.supplier_invoice_schema.mark_migration_applied", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        get_conn(1)
    with sqlite3.connect(database) as conn:
        assert migrations.get_schema_version(conn) == 23
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='supplier_invoice_confirmations'").fetchone() is None
    saved, = backups(database)
    original = saved.read_bytes()
    assert_record(database)
    monkeypatch.setattr("app.services.supplier_invoice_schema.mark_migration_applied", real_mark)
    get_conn(1).close()
    assert len(backups(database)) == 2
    assert saved.read_bytes() == original


def test_concurrent_current_connections_do_not_backup(database):
    barrier = Barrier(8)
    def connect(_):
        barrier.wait(timeout=10)
        conn = get_conn(1)
        try:
            return conn.execute("SELECT current_stock FROM products WHERE code='KEEP'").fetchone()[0]
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(connect, range(8))) == [7] * 8
    assert backups(database) == []


def test_concurrent_backups_are_unique_and_preserve_existing(database):
    directory = database.parent / ".backups"
    directory.mkdir()
    existing = directory / "existing.db"
    existing.write_bytes(b"existing backup must survive")
    barrier = Barrier(8)
    def backup(_):
        barrier.wait(timeout=10)
        return migrations.backup_database(str(database))
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(backup, range(8)))
    assert len(set(paths)) == 8
    assert existing.read_bytes() == b"existing backup must survive"
    for path in paths:
        assert_record(path)


def test_new_database_needs_no_backup(tmp_path):
    path = tmp_path / "new.db"
    assert migrations.migrate_tenant_db(1, str(path)) is None
    assert backups(path) == []


def test_backup_includes_committed_wal_records(database):
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE products SET current_stock=19 WHERE code='KEEP'")
        conn.commit()
        saved = migrations.backup_database(str(database))
        with sqlite3.connect(saved) as backup:
            assert backup.execute("SELECT current_stock FROM products WHERE code='KEEP'").fetchone()[0] == 19
    finally:
        conn.close()


def test_concurrent_pending_connections_preserve_backups(database, monkeypatch):
    pending(database)
    barrier = Barrier(4)
    def connect(_):
        barrier.wait(timeout=10)
        conn = get_conn(1)
        try:
            return migrations.get_schema_version(conn)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(connect, range(4))) == [28] * 4
    saved = backups(database)
    assert len(saved) == 1
    for path in saved:
        assert_record(path)
    assert_record(database)
