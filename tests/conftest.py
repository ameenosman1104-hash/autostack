"""Test isolation for AutoStack.

Rules enforced here:
- No test writes to the repository's normal data/ folder. Module-level database setup runs
  against a throwaway session folder, and every test gets its own folder.
- AUTOSTACK_DATA_DIR and the main-database path are restored after every test, including values
  that tests assign directly to os.environ.
- If the repository's data/main.db is changed during the session, the session fails.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_DATA = (Path(__file__).resolve().parent.parent / "data").resolve()
REPO_MAIN_DB = REPO_DATA / "main.db"

# Set before any application module is imported. Importing app.main_db creates its database file
# at import time, so this must happen at collection, not inside a fixture.
SESSION_DIR = Path(tempfile.mkdtemp(prefix="autostack-tests-"))
atexit.register(shutil.rmtree, SESSION_DIR, ignore_errors=True)
os.environ["AUTOSTACK_DATA_DIR"] = str(SESSION_DIR)
os.environ["AUTOSTACK_ENV"] = "test"
os.environ.pop("RENDER", None)

# Import the database module now, while the session folder is set. If a test class were the first to
# import it, the module would keep that class's temporary folder after the class deletes it.
import app.main_db  # noqa: E402,F401


def _snapshot(path):
    if not path.exists():
        return ("missing",)
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


@pytest.fixture(scope="session", autouse=True)
def repository_data_is_untouched():
    before = _snapshot(REPO_MAIN_DB)
    yield
    after = _snapshot(REPO_MAIN_DB)
    assert before == after, f"the test session modified the repository database {REPO_MAIN_DB}"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    """Give each test its own data folder. monkeypatch restores everything, even raw os.environ writes."""
    # A separate folder, not a child of tmp_path, so tests that inspect their own tmp_path see only what they create.
    data = tmp_path_factory.mktemp("autostack-data")
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(data))
    monkeypatch.setenv("AUTOSTACK_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    main_db = sys.modules.get("app.main_db")
    if main_db is not None:
        monkeypatch.setattr(main_db, "MAIN_DB", str(data / "main.db"))
        # Build the same schema create_app() builds, so every test starts from a valid database.
        main_db.init_main_db()
        google_auth = sys.modules.get("app.google_auth")
        if google_auth is not None:
            conn = main_db.get_conn()
            try:
                google_auth.ensure_identity_table(conn)
            finally:
                conn.close()
    # create_app() starts a daemon thread that polls the main database every 15 seconds. A thread
    # started by one test would keep reading a folder that is deleted after that test ends.
    from app.services import auto_sync
    monkeypatch.setattr(auto_sync, "start_background_sync", lambda app: None)
    yield data
    current = Path(os.environ.get("AUTOSTACK_DATA_DIR", ""))
    assert REPO_DATA not in current.resolve().parents and current.resolve() != REPO_DATA, \
        "a test pointed AUTOSTACK_DATA_DIR at the repository data folder"
