"""Synthetic-only Linux CI entry point. Never imports the application factory."""
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile

FIX_COMMIT = "6c54dcf066ab2a6cd3e90df1537f858169b0d7a2"
SOURCE_HASHES = {
    "app/__init__.py": "16956a175a1320b1c93cc998bfb11ea4ad95e4124e4d0f7de408c1e30f411d16",
    "app/db_migrations.py": "86ed3167d61e65938cdef2b60628e1f90908c2773d22c5b878d440f9030e7c9c",
    "app/tenant_db.py": "9ea1798f8aaca6db4a4f45800f21af87d1febd856a8db3201356d68816310eeb",
    "app/credential_store.py": "42c9f15790c5ebea4b215a4d67140a19022f1c727906528421971101bf095a95"
}
TEST_FILES = ("tests/test_migration_backups.py", "tests/test_migration_concurrency.py")


def main():
    if sys.platform != "linux" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Requires Linux and Python 3.12; no fallback is permitted")
    allowed_environment = {
        "PATH", "LANG", "TMPDIR", "LD_LIBRARY_PATH",
        "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "BACKUPFIX_SYNTHETIC_ONLY",
    }
    if set(os.environ) - allowed_environment:
        raise SystemExit("Unexpected environment variables; run through the isolated workflow")
    expected_library = str(Path(sys.base_prefix) / "lib")
    if os.environ.get("LD_LIBRARY_PATH") != expected_library:
        raise SystemExit("Only the selected Python installation's library path is allowed")
    if os.environ.get("BACKUPFIX_SYNTHETIC_ONLY") != "1":
        raise SystemExit("Synthetic-only workflow flag missing")
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1":
        raise SystemExit("Third-party pytest plugin auto-loading must be disabled")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise SystemExit("Requires a private network namespace with only loopback")
    if "fork" not in mp.get_all_start_methods():
        raise SystemExit("Linux fork coverage is unavailable")

    repo = Path(__file__).resolve().parents[1]
    for name, expected in SOURCE_HASHES.items():
        source = (repo / name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(source).hexdigest() != expected:
            raise SystemExit(f"Source differs from approved fix {FIX_COMMIT}: {name}")
    if any((repo / name).exists() for name in (".env", ".env.production", "data")):
        raise SystemExit("Sensitive files/directories present; use the sparse checkout")
    if any(path.is_file() and path.suffix.lower() in (".db", ".sqlite", ".sqlite3", ".backup", ".enc", ".key")
           for path in repo.rglob("*") if ".git" not in path.relative_to(repo).parts):
        raise SystemExit("Database, backup or key files present in source checkout")

    state = Path(tempfile.mkdtemp(prefix="autostack-synthetic-"))
    (state / "home").mkdir()
    os.environ.update(
        HOME=str(state / "home"), AUTOSTACK_DATA_DIR=str(state / "application-data"),
        AUTOSTACK_ENV="test", TESTING="1", PYTEST_ADDOPTS="",
    )
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    print("Approved source:", FIX_COMMIT)
    print("Python:", sys.version.split()[0], "SQLite:", sqlite3.sqlite_version)
    print("Synthetic-only state:", state)

    import pytest

    class Coverage:
        def __init__(self):
            self.methods = set()
            self.passed = 0
            self.skipped = 0

        def pytest_runtest_logreport(self, report):
            if report.skipped:
                self.skipped += 1
            if report.when == "call" and report.passed:
                self.passed += 1
                for method in ("spawn", "fork"):
                    if method in report.nodeid:
                        self.methods.add(method)

        def pytest_sessionfinish(self, session, exitstatus):
            if exitstatus == 0 and (self.methods != {"spawn", "fork"}
                                    or self.skipped or self.passed != 37):
                print("Incomplete Linux coverage:", self.methods, self.passed, self.skipped)
                session.exitstatus = 1

    # Only the two migration files are collected. No application is started.
    # Keep synthetic files until the disposable VM is discarded; upload no DBs.
    return pytest.main([
        "-vv", "-p", "no:cacheprovider", "--strict-markers",
        "--basetemp", str(state / "pytest"),
        "--junitxml", str(state / "results.xml"), *TEST_FILES,
    ], plugins=[Coverage()])


if __name__ == "__main__":
    raise SystemExit(main())
