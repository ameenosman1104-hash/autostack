"""Real spawned-process migration tests. Disposable databases and backups only."""
import importlib
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import sys

import pytest

CTX = mp.get_context("spawn")
LATEST = 18


@pytest.fixture(params=[
    "spawn",
    pytest.param("fork", marks=pytest.mark.skipif(
        sys.platform != "linux" or "fork" not in mp.get_all_start_methods(),
        reason="Linux fork coverage requires a Linux host",
    )),
], autouse=True)
def process_context(request, monkeypatch, tmp_path):
    # No application, SQLite connection or background worker is started before
    # forking. Exercise both Gunicorn-like fork and independent spawn workers.
    monkeypatch.setattr(sys.modules[__name__], "CTX", mp.get_context(request.param))
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path / "synthetic-app-data"))


def mig():
    return importlib.import_module("app.db_migrations")


def worker(path, barrier, results, fail_version=None, entered=None, release=None,
           timeout=10, use_get_conn=False, crash=False):
    from app import db_migrations as db
    db.MIGRATION_BUSY_TIMEOUT_SECONDS = timeout
    if entered is not None:
        real_backup = db.backup_database
        def paused_backup(path):
            entered.set()
            if not release.wait(15):
                raise RuntimeError("test backup wait expired")
            return real_backup(path)
        db.backup_database = paused_backup
    if fail_version is not None:
        real_mark = db.mark_migration_applied
        def fail(conn, version, name):
            real_mark(conn, version, name)
            if version == fail_version:
                if crash:
                    os._exit(17)
                raise RuntimeError("injected migration failure")
        db.mark_migration_applied = fail
    try:
        if barrier is not None:
            barrier.wait(timeout=15)
        if use_get_conn:
            os.environ["AUTOSTACK_DATA_DIR"] = str(Path(path).parent.parent)
            from app.tenant_db import get_conn
            conn = get_conn(1)
            conn.close()
            saved = None
        else:
            saved = db.migrate_tenant_db(1, path)
        with sqlite3.connect(path) as conn:
            version = db.get_schema_version(conn)
        results.put(("ok", saved, version))
    except Exception as e:
        results.put(("error", str(e), None))


def join(process):
    process.join(25)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Migration worker hung")
    assert process.exitcode == 0


def seed(path, version):
    """Create a current database, then rewind it to an earlier schema version with data."""
    mig().migrate_tenant_db(1, str(path))
    with sqlite3.connect(path) as conn:
        if version < LATEST:
            conn.execute("DROP TABLE invoice_sequences")
            conn.execute("DELETE FROM schema_migrations WHERE version>=18")
        conn.execute("INSERT INTO products(code,name,current_stock) VALUES ('KEEP','Keep me',7)")
        conn.commit()


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return (conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall(),
                conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall(),
                conn.execute("SELECT * FROM products ORDER BY id").fetchall())


def saved_files(path):
    return sorted((path.parent / ".backups").glob("*.db"))


def assert_current(path):
    with sqlite3.connect(path) as conn:
        assert mig().get_schema_version(conn) == LATEST
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(v,) for v in range(1, LATEST + 1)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("version", [None, 17])
@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_two_workers_apply_once(tmp_path, version, journal):
    path = tmp_path / "tenants" / "1.db"
    path.parent.mkdir(parents=True)
    if version is not None:
        seed(path, version)
        before = snapshot(path)
        existing_dir = path.parent / ".backups"
        existing_dir.mkdir(exist_ok=True)
        sentinel = existing_dir / "existing.db"
        sentinel.write_bytes(b"preserve this existing backup")
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA journal_mode={journal}")
    barrier, results = CTX.Barrier(2), CTX.Queue()
    processes = [CTX.Process(target=worker, args=(str(path), barrier, results)) for _ in range(2)]
    for process in processes:
        process.start()
    try:
        for process in processes:
            join(process)
        outcomes = [results.get(timeout=5) for _ in processes]
        assert all(result[0] == "ok" for result in outcomes), outcomes
        assert_current(path)
        if version is None:
            assert saved_files(path) == []
        else:
            created = [result[1] for result in outcomes if result[1]]
            assert len(created) == 1
            assert snapshot(created[0]) == before
            assert sentinel.read_bytes() == b"preserve this existing backup"
            with sqlite3.connect(path) as conn:
                assert conn.execute("SELECT current_stock FROM products WHERE code='KEEP'").fetchone()[0] == 7
        count = len(saved_files(path))
        assert mig().migrate_tenant_db(1, str(path)) is None
        assert len(saved_files(path)) == count
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate(); process.join(5)
        results.close()


@pytest.mark.parametrize("version,failure", [(None, 9), (None, 18), (17, 18)])
def test_failure_rolls_back_entire_upgrade_and_retries(tmp_path, version, failure):
    path = tmp_path / "tenants" / "1.db"
    path.parent.mkdir(parents=True)
    if version is not None:
        seed(path, version)
        before = snapshot(path)
    results = CTX.Queue()
    process = CTX.Process(target=worker, args=(str(path), None, results, failure))
    process.start(); join(process)
    outcome = results.get(timeout=5)
    assert outcome[0] == "error" and "injected migration failure" in outcome[1]
    if version is None:
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        assert saved_files(path) == []
    else:
        assert snapshot(path) == before
        saved, = saved_files(path)
        assert snapshot(saved) == before
        saved_bytes = saved.read_bytes()
    mig().migrate_tenant_db(1, str(path))
    assert_current(path)
    if version is not None:
        assert len(saved_files(path)) == 2
        assert saved.read_bytes() == saved_bytes
    results.close()


@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_backup_lock_excludes_other_writers_and_times_out_safely(tmp_path, journal):
    path = tmp_path / "tenant.db"
    seed(path, 17)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA journal_mode={journal}")
    before = snapshot(path)
    entered, release, results = CTX.Event(), CTX.Event(), CTX.Queue()
    first = CTX.Process(target=worker, args=(str(path), None, results, None, entered, release))
    first.start()
    try:
        assert entered.wait(15), "first worker did not reach backup under the lock"
        with sqlite3.connect(path, timeout=0.1) as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("UPDATE products SET current_stock=99 WHERE code='KEEP'")
        second = CTX.Process(target=worker, args=(str(path), None, results, None, None, None, 0.1))
        second.start(); join(second)
        failure = results.get(timeout=5)
        assert failure[0] == "error" and "locked" in failure[1], failure
        assert snapshot(path) == before
        assert saved_files(path) == []
        release.set(); join(first)
        success = results.get(timeout=5)
        assert success[0] == "ok", success
        assert snapshot(success[1]) == before
        assert_current(path)
        assert mig().migrate_tenant_db(1, str(path)) is None
    finally:
        release.set()
        if first.is_alive():
            first.terminate(); first.join(5)
        results.close()


def test_worker_crash_rolls_back_and_releases_lock(tmp_path):
    path = tmp_path / "tenant.db"
    seed(path, 17)
    before = snapshot(path)
    results = CTX.Queue()
    process = CTX.Process(target=worker, args=(str(path), None, results, 18, None, None, 10, False, True))
    process.start(); process.join(25)
    if process.is_alive():
        process.terminate(); process.join(5)
    assert process.exitcode == 17
    assert snapshot(path) == before
    saved, = saved_files(path)
    assert snapshot(saved) == before
    mig().migrate_tenant_db(1, str(path))
    assert_current(path)
    results.close()


def test_simultaneous_ordinary_connections_create_no_backups(tmp_path):
    path = tmp_path / "tenants" / "1.db"
    path.parent.mkdir(parents=True)
    mig().migrate_tenant_db(1, str(path))
    barrier, results = CTX.Barrier(2), CTX.Queue()
    processes = [CTX.Process(target=worker, args=(str(path), barrier, results, None, None, None, 10, True)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        join(process)
    outcomes = [results.get(timeout=5) for _ in processes]
    assert all(result[0] == "ok" for result in outcomes), outcomes
    assert saved_files(path) == []
    results.close()


def test_commit_busy_rolls_back_every_pending_version(tmp_path):
    path = tmp_path / "tenant.db"
    seed(path, 17)
    before = snapshot(path)
    entered, release, results = CTX.Event(), CTX.Event(), CTX.Queue()
    process = CTX.Process(target=worker, args=(str(path), None, results, None, entered, release, 0.1))
    process.start()
    reader = None
    try:
        assert entered.wait(15)
        reader = sqlite3.connect(path)
        reader.execute("BEGIN")
        assert mig().get_schema_version(reader) == 17
        release.set(); join(process)
        result = results.get(timeout=5)
        assert result[0] == "error" and "locked" in result[1], result
        reader.rollback(); reader.close(); reader = None
        assert snapshot(path) == before
        saved, = saved_files(path)
        assert snapshot(saved) == before
        mig().migrate_tenant_db(1, str(path))
        assert_current(path)
    finally:
        if reader is not None:
            reader.close()
        release.set()
        if process.is_alive():
            process.terminate(); process.join(5)
        results.close()


def test_waiting_worker_retries_after_first_worker_failure(tmp_path):
    path = tmp_path / "tenant.db"
    seed(path, 17)
    before = snapshot(path)
    entered, release, results = CTX.Event(), CTX.Event(), CTX.Queue()
    first = CTX.Process(target=worker, args=(str(path), None, results, 18, entered, release))
    second = CTX.Process(target=worker, args=(str(path), None, results))
    first.start()
    try:
        assert entered.wait(15)
        second.start()
        release.set()
        join(first); join(second)
        outcomes = [results.get(timeout=5) for _ in range(2)]
        assert sorted(result[0] for result in outcomes) == ["error", "ok"], outcomes
        assert any("injected migration failure" in result[1] for result in outcomes if result[0] == "error")
        assert_current(path)
        assert len(saved_files(path)) == 2
        for saved in saved_files(path):
            assert snapshot(saved) == before
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None and process.is_alive():
                process.terminate(); process.join(5)
        results.close()


def repeated_connection_worker(path, barrier, results):
    os.environ["AUTOSTACK_DATA_DIR"] = str(Path(path).parent.parent)
    from app.tenant_db import get_conn
    try:
        barrier.wait(timeout=15)
        for _ in range(50):
            conn = get_conn(1)
            try:
                assert conn.execute("SELECT current_stock FROM products WHERE code='KEEP'").fetchone()[0] == 7
            finally:
                conn.close()
        results.put(("ok", 50))
    except Exception as error:
        results.put(("error", str(error)))


def test_repeated_worker_connections_create_zero_backups(tmp_path):
    path = tmp_path / "tenants" / "1.db"
    path.parent.mkdir(parents=True)
    seed(path, LATEST)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    barrier, results = CTX.Barrier(2), CTX.Queue()
    processes = [CTX.Process(target=repeated_connection_worker,
                             args=(str(path), barrier, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            join(process)
        assert [results.get(timeout=5) for _ in processes] == [("ok", 50)] * 2
        assert not (path.parent / ".backups").exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
