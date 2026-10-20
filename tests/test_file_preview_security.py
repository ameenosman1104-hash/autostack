"""File preview security: the server never opens a browser-supplied path.

Preview and connection-test requests carry the file contents. Anything that looks like a server path is refused,
and each tenant only sees its own connections.
"""
import importlib.util
import io
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import init_tenant_db
from app.services.data_sources import create_data_source, get_data_source

PASSWORD = "preview-test-password"
CSV_TEXT = "Name,Invoice No.,email\nShop,INV-1,owner@example.com\n"
PATH_REFUSED = "Upload the file itself"


@pytest.fixture
def app():
    test_app = create_app()
    test_app.config["TESTING"] = True
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _tenant(name):
    username = f"{name}_{uuid4().hex[:10]}"
    tid, err = create_tenant(f"{name} Shop", username, f"{username}@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    return tid, username


def _anonymous_csrf(client):
    """Send the CSRF token from the login page, as the browser does for every request."""
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    client.environ_base["HTTP_X_CSRFTOKEN"] = token


def _login(client, username):
    _anonymous_csrf(client)
    token = client.environ_base["HTTP_X_CSRFTOKEN"]
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": token},
                           follow_redirects=True)
    meta = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert meta, "logged-in page should expose a CSRF token"
    client.environ_base["HTTP_X_CSRFTOKEN"] = meta.group(1)


@pytest.fixture
def shop_a(app, client):
    tid, username = _tenant("alpha")
    _login(client, username)
    return tid


def _upload(client, text=CSV_TEXT, name="debtors.csv", url="/api/local-file/preview", extra=None):
    data = {"file": (io.BytesIO(text.encode("utf-8") if isinstance(text, str) else text), name)}
    data.update(extra or {})
    return client.post(url, data=data, content_type="multipart/form-data")


def _json_path(client, file_path, url="/api/local-file/preview"):
    return client.post(url, json={"file_path": file_path}, content_type="application/json")


# ── Valid and legitimate workflows ─────────────────────────────────────────────

def test_authorised_csv_upload_is_previewed(client, shop_a):
    response = _upload(client)
    assert response.status_code == 200
    data = response.get_json()
    assert data["preview"][0]["invoice"] == "INV-1"
    assert data["columns"] and data["total_rows"] == 1


def test_authorised_xlsx_upload_is_previewed(client, shop_a):
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["Name", "Invoice No."])
    book.active.append(["Shop", "INV-9"])
    buffer = io.BytesIO()
    book.save(buffer)
    response = _upload(client, text=buffer.getvalue(), name="whatever.bin")
    assert response.status_code == 200
    assert response.get_json()["preview"][0]["invoice"] == "INV-9"


def test_data_source_preview_accepts_an_upload(client, shop_a):
    response = _upload(client, url="/api/data-sources/preview", extra={"source_type": "local_file"})
    assert response.status_code == 200
    assert response.get_json()["preview"][0]["invoice"] == "INV-1"


def test_connection_check_accepts_an_upload(client, app, shop_a):
    _, source_id = create_data_source(shop_a, "Debtors", "local_file", {"file_path": "C:/debtors.csv"})
    response = _upload(client, url=f"/api/connections/{source_id}/test")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_connector_still_reads_its_own_local_file(tmp_path):
    """The Windows connector keeps its own copy of the reader and reads the file on the user's PC."""
    spec = importlib.util.spec_from_file_location(
        "connector_snapshot", Path(__file__).parents[1] / "connector" / "file_snapshot.py")
    connector_snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(connector_snapshot)
    local = tmp_path / "debtors.csv"
    local.write_text(CSV_TEXT, encoding="utf-8")
    assert connector_snapshot.read_snapshot(str(local))[0]["invoice"] == "INV-1"


# ── Authentication ─────────────────────────────────────────────────────────────

def test_missing_authentication_is_refused(app, client):
    _anonymous_csrf(client)
    response = _upload(client)
    assert response.status_code == 302
    assert '"preview"' not in response.get_data(as_text=True)


def test_data_source_preview_requires_authentication(app, client):
    _anonymous_csrf(client)
    response = _upload(client, url="/api/data-sources/preview", extra={"source_type": "local_file"})
    assert response.status_code == 302


# ── Path references ────────────────────────────────────────────────────────────

def test_invalid_file_reference_is_refused(client, shop_a):
    response = _json_path(client, "debtors.csv")
    assert response.status_code == 400
    assert PATH_REFUSED in response.get_json()["error"]


def test_absolute_server_path_is_refused(client, shop_a, tmp_path):
    target = tmp_path / "server_only.csv"
    target.write_text(CSV_TEXT, encoding="utf-8")
    response = _json_path(client, str(target))
    assert response.status_code == 400
    assert "preview" not in response.get_json()


def test_absolute_path_in_form_field_is_refused(client, shop_a, tmp_path):
    target = tmp_path / "server_only.csv"
    target.write_text(CSV_TEXT, encoding="utf-8")
    response = _upload(client, extra={"file_path": str(target)})
    assert response.status_code == 400
    assert PATH_REFUSED in response.get_json()["error"]


@pytest.mark.parametrize("path", ["../../secret.csv", "..\\..\\secret.csv", "../../../etc/passwd.csv"])
def test_parent_directory_traversal_is_refused(client, shop_a, path):
    response = _json_path(client, path)
    assert response.status_code == 400
    assert "preview" not in response.get_json()


def test_upload_filename_is_never_used_as_a_path(client, shop_a, tmp_path):
    before = sorted(os.listdir(tmp_path))
    response = _upload(client, name="../../escape.csv")
    assert response.status_code == 200
    assert sorted(os.listdir(tmp_path)) == before


def test_symlink_escape_is_refused(client, shop_a, tmp_path):
    """A link inside the tenant area that points outside it must not be followed."""
    import subprocess
    outside = tmp_path / "outside"
    inside = tmp_path / "inside"
    outside.mkdir()
    inside.mkdir()
    (outside / "secret.csv").write_text(CSV_TEXT, encoding="utf-8")
    link_file = inside / "link.csv"
    link_dir = inside / "linked_dir"
    try:
        os.symlink(outside / "secret.csv", link_file)
        os.symlink(outside, link_dir, target_is_directory=True)
    except OSError:
        # Windows needs a privilege for symlinks. A directory junction needs none and is also a reparse-point escape.
        link_file.unlink(missing_ok=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link_dir), str(outside)],
                       check=True, capture_output=True)
        link_file = link_dir / "secret.csv"
    try:
        for reference in (str(link_file), str(link_dir / "secret.csv")):
            response = _json_path(client, reference)
            assert response.status_code == 400
            assert "preview" not in response.get_json()
    finally:
        # Remove the link itself, never its contents, so cleanup cannot reach the target folder.
        if link_dir.exists():
            os.rmdir(link_dir)
        if link_file.is_symlink():
            link_file.unlink()


# ── Tenant isolation ───────────────────────────────────────────────────────────

def test_one_tenant_cannot_use_another_tenants_connection(app, client, shop_a):
    other_tid, other_user = _tenant("beta")
    _, other_source = create_data_source(other_tid, "Beta debtors", "local_file", {"file_path": "C:/beta/debtors.csv"})
    response = client.post(f"/api/connections/{other_source}/test", data={})
    assert response.status_code == 404
    assert get_data_source(other_tid, other_source) is not None


def test_one_tenant_cannot_delete_another_tenants_connection(app, client, shop_a):
    other_tid, _ = _tenant("gamma")
    _, other_source = create_data_source(other_tid, "Gamma debtors", "local_file", {"file_path": "C:/gamma/debtors.csv"})
    client.delete(f"/api/connections/{other_source}")
    assert get_data_source(other_tid, other_source) is not None


def test_another_tenants_stored_path_is_not_previewed(client, shop_a, tmp_path):
    other_tid, _ = _tenant("delta")
    stored = tmp_path / "delta-debtors.csv"
    stored.write_text(CSV_TEXT, encoding="utf-8")
    create_data_source(other_tid, "Delta", "local_file", {"file_path": str(stored)})
    response = _json_path(client, str(stored))
    assert response.status_code == 400


def test_connection_check_never_reads_the_stored_path(client, shop_a, tmp_path):
    """A saved connection points at the user's own computer. Checking it without an upload must not read the path."""
    stored = tmp_path / "saved.csv"
    stored.write_text(CSV_TEXT, encoding="utf-8")
    _, source_id = create_data_source(shop_a, "Own", "local_file", {"file_path": str(stored)})
    response = client.post(f"/api/connections/{source_id}/test", data={})
    assert response.status_code == 400
    assert "Upload the file" in response.get_json()["error"]


# ── Formats and sizes ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff",
    b"Name,Invoice No.\x00\x00\x00binary",
])
def test_unsupported_content_is_refused_whatever_the_name(client, shop_a, payload):
    response = _upload(client, text=payload, name="debtors.xlsx")
    assert response.status_code == 400


def test_zip_without_a_workbook_is_refused(client, shop_a):
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "not a workbook")
    response = _upload(client, text=buffer.getvalue(), name="debtors.csv")
    assert response.status_code == 400


def test_oversized_upload_is_refused(client, shop_a):
    response = _upload(client, text=b"A" * (10 * 1024 * 1024 + 1), name="huge.csv")
    assert response.status_code in (400, 413)
    assert "preview" not in (response.get_json(silent=True) or {})


def test_oversized_content_is_refused_by_the_reader():
    from app.services import file_snapshot
    with pytest.raises(ValueError, match="10 MB"):
        file_snapshot.read_snapshot_bytes(b"A" * (file_snapshot.MAX_BYTES + 1))


def test_missing_file_is_refused(client, shop_a):
    response = client.post("/api/local-file/preview", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "file upload required" in response.get_json()["error"]
