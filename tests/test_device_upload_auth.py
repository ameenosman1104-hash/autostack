"""Windows connector authentication: device upload endpoints use pairing tokens, not browser sessions.

The upload endpoints are exempt from CSRF because they never read the session cookie. These tests check
that the exemption does not weaken them, and that browser pairing still needs login and CSRF.
"""
import re

import pytest

from app import create_app
from app.main_db import create_tenant
from app.tenant_db import init_tenant_db
from app.services.local_file_sync import create_local_connection, revoke_connection

PASSWORD = "device-test-password"


@pytest.fixture
def app():
    test_app = create_app()
    test_app.config["TESTING"] = True
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def tenant(app):
    """A tenant with its own database and one local-file connection, returning (tid, pairing_token, connection_id)."""
    tid, err = create_tenant("Device Shop", "device_shop", "device@example.com", PASSWORD)
    assert not err
    init_tenant_db(tid)
    token = create_local_connection(tid, "Debtors", "C:/debtors.csv", None, {"email": "Email"}, "Invoice No.")
    return tid, token


def _connection_id(tid):
    from app.tenant_db import get_conn
    conn = get_conn(tid)
    try:
        return conn.execute("SELECT id FROM local_file_connections ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _login(client, username="device_shop"):
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": username, "password": PASSWORD,
                                           "csrf_token": token}, follow_redirects=True)
    assert response.status_code == 200


def _upload(client, token=None):
    headers = {"X-Pairing-Token": token} if token is not None else {}
    return client.post("/api/local-file/upload", json={"snapshot": []}, headers=headers,
                       content_type="application/json")


class TestDeviceUpload:
    def test_valid_token_is_accepted_without_a_csrf_token(self, client, tenant):
        _, token = tenant
        response = _upload(client, token)
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"

    def test_missing_token_is_rejected_with_401_not_csrf_400(self, client, tenant):
        response = _upload(client)
        assert response.status_code == 401
        assert "pairing token" in response.get_json()["error"].lower()

    def test_invalid_token_is_rejected(self, client, tenant):
        response = _upload(client, "0" * 32)
        assert response.status_code == 401
        assert "invalid or revoked" in response.get_json()["error"].lower()

    def test_revoked_token_is_rejected(self, client, tenant):
        tid, token = tenant
        assert revoke_connection(tid, _connection_id(tid))
        response = _upload(client, token)
        assert response.status_code == 401
        assert "invalid or revoked" in response.get_json()["error"].lower()

    def test_logged_in_browser_session_cannot_replace_the_token(self, client, tenant):
        _login(client)
        response = _upload(client)
        assert response.status_code == 401

    def test_data_source_upload_rejects_missing_and_invalid_tokens(self, client, tenant):
        for headers in ({}, {"X-Pairing-Token": "0" * 32}):
            response = client.post("/api/data-sources/upload", json={"snapshot": []},
                                   headers=headers, content_type="application/json")
            assert response.status_code == 401


class TestBrowserPairingStillProtected:
    def test_anonymous_pairing_without_csrf_is_rejected(self, client):
        response = client.post("/api/local-file/pair", json={"name": "X", "file_path": "C:/x.csv"},
                               content_type="application/json")
        assert response.status_code == 400

    def test_anonymous_pairing_with_csrf_redirects_to_login(self, client, tenant):
        page = client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post("/api/local-file/pair", json={"name": "X", "file_path": "C:/x.csv"},
                               headers={"X-CSRFToken": csrf}, content_type="application/json")
        assert response.status_code in (302, 401)
        assert "pairing_token" not in (response.get_json(silent=True) or {})

    def test_logged_in_pairing_without_csrf_is_rejected(self, client, tenant):
        _login(client)
        response = client.post("/api/local-file/pair", json={"name": "X", "file_path": "C:/x.csv"},
                               content_type="application/json")
        assert response.status_code == 400
