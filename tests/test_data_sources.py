"""Tests for unified data source management (local, hosted URL, API)."""
import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from app import create_app
from app.main_db import get_conn as get_main_conn
from app.tenant_db import get_conn
from app.services.data_sources import (
    create_data_source,
    list_data_sources,
    get_data_source,
    lookup_tenant_by_token,
    disconnect_source,
    update_sync_status,
    record_change,
    detect_conflict,
    resolve_conflict,
    get_unresolved_conflicts,
    validate_url_config,
    validate_api_config,
    validate_local_file_config,
)


@pytest.fixture
def app():
    """Create test app."""
    test_app = create_app()
    test_app.config["TESTING"] = True
    with test_app.app_context():
        yield test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def auth_user(client, app):
    """Create and authenticate a test user."""
    from app.main_db import create_tenant

    with app.app_context():
        tid, err = create_tenant("Test Business", "testuser", "test@example.com", "password123")
        assert not err

        response = client.post(
            "/login",
            data={"username": "testuser", "password": "password123"},
            follow_redirects=True
        )
        assert response.status_code == 200

    return tid


class TestValidation:
    """Test configuration validation for all source types."""

    def test_validate_url_config_https(self):
        """URL must use HTTPS."""
        assert validate_url_config({"url": "https://example.com/data.csv"})

    def test_validate_url_config_http_fails(self):
        """HTTP URLs rejected."""
        with pytest.raises(ValueError, match="HTTPS"):
            validate_url_config({"url": "http://example.com/data.csv"})

    def test_validate_url_config_empty_fails(self):
        """Empty URL rejected."""
        with pytest.raises(ValueError, match="required"):
            validate_url_config({"url": ""})

    def test_validate_api_config_https(self):
        """API URL must use HTTPS."""
        assert validate_api_config({"api_url": "https://api.example.com/debtors"})

    def test_validate_api_config_auth_types(self):
        """Supported auth types: bearer, api_key, basic."""
        for auth in ("bearer", "api_key", "basic"):
            assert validate_api_config({"api_url": "https://api.example.com", "auth_type": auth})

    def test_validate_api_config_invalid_auth(self):
        """Unsupported auth types rejected."""
        with pytest.raises(ValueError, match="auth type"):
            validate_api_config({"api_url": "https://api.example.com", "auth_type": "oauth2"})

    def test_validate_local_file_config_csv(self):
        """CSV files accepted."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            assert validate_local_file_config({"file_path": path})
        finally:
            os.unlink(path)

    def test_validate_local_file_config_xlsx(self):
        """XLSX files accepted."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        try:
            assert validate_local_file_config({"file_path": path})
        finally:
            os.unlink(path)

    def test_validate_local_file_config_wrong_type_fails(self):
        """Non-CSV/XLSX files rejected."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="CSV or XLSX"):
                validate_local_file_config({"file_path": path})
        finally:
            os.unlink(path)


class TestDataSourceConnections:
    """Test data source connection creation and management."""

    def test_create_local_file_source(self, app, auth_user):
        """Create local file data source."""
        tid = auth_user
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with app.app_context():
                token, source_id = create_data_source(
                    tid,
                    "Test Local",
                    "local_file",
                    {"file_path": path},
                    {"email": "Email"},
                    "Invoice No."
                )

                assert token
                assert source_id
                assert len(token) == 32

                source = get_data_source(tid, source_id)
                assert source["name"] == "Test Local"
                assert source["source_type"] == "local_file"
        finally:
            os.unlink(path)

    def test_create_hosted_url_source(self, app, auth_user):
        """Create hosted URL data source."""
        tid = auth_user

        with app.app_context():
            token, source_id = create_data_source(
                tid,
                "Test Hosted",
                "hosted_url",
                {"url": "https://example.com/debtors.csv"},
                {"email": "Email"},
                "Invoice No."
            )

            assert source_id
            assert token is None  # No pairing token for URL sources

            source = get_data_source(tid, source_id)
            assert source["source_type"] == "hosted_url"

    def test_create_api_source(self, app, auth_user):
        """Create API data source."""
        tid = auth_user

        with app.app_context():
            token, source_id = create_data_source(
                tid,
                "Test API",
                "api",
                {
                    "api_url": "https://api.example.com/debtors",
                    "auth_type": "bearer",
                    "api_key": "test-key"
                },
                {"email": "email_address"},
                "debtor_id"
            )

            assert source_id
            assert token is None

            source = get_data_source(tid, source_id)
            assert source["source_type"] == "api"
            assert source["config"]["auth_type"] == "bearer"

    def test_list_data_sources(self, app, auth_user):
        """List all data sources for a tenant."""
        tid = auth_user

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with app.app_context():
                create_data_source(tid, "Source 1", "local_file", {"file_path": path})
                create_data_source(tid, "Source 2", "hosted_url", {"url": "https://example.com/data.csv"})

                sources = list_data_sources(tid)
                assert len(sources) == 2
                assert sources[0]["name"] in ("Source 1", "Source 2")
                assert sources[0]["status"] == "active"
        finally:
            os.unlink(path)

    def test_disconnect_source(self, app, auth_user):
        """Disconnect (revoke) a data source."""
        tid = auth_user

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with app.app_context():
                _, source_id = create_data_source(tid, "Test", "local_file", {"file_path": path})

                success = disconnect_source(tid, source_id)
                assert success

                source = get_data_source(tid, source_id)
                assert source is None  # Revoked sources are not returned

        finally:
            os.unlink(path)

    def test_local_file_pairing_token_registered(self, app, auth_user):
        """Pairing token for local files is registered in main database."""
        tid = auth_user

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with app.app_context():
                token, source_id = create_data_source(tid, "Test", "local_file", {"file_path": path})

                # Verify token is in main database
                looked_up_tid, looked_up_source_id = lookup_tenant_by_token(token)
                assert looked_up_tid == tid
                assert looked_up_source_id == source_id
        finally:
            os.unlink(path)


class TestConflictHandling:
    """Test conflict detection and resolution."""

    def test_detect_conflict(self, app, auth_user):
        """Detect a conflict between AutoStack and source."""
        tid = auth_user

        with app.app_context():
            _, source_id = create_data_source(
                tid,
                "Test",
                "hosted_url",
                {"url": "https://example.com/data.csv"}
            )

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="test@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"hosted_url:{source_id}"
            )

            detect_conflict(
                tid,
                source_id,
                did,
                "INV001",
                autostack_values={"email": "autostack@example.com"},
                source_values={"email": "source@example.com"}
            )

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 1
            assert conflicts[0]["invoice_id"] == "INV001"

    def test_resolve_conflict_keep_autostack(self, app, auth_user):
        """Resolve conflict by keeping AutoStack value."""
        tid = auth_user

        with app.app_context():
            _, source_id = create_data_source(
                tid,
                "Test",
                "hosted_url",
                {"url": "https://example.com/data.csv"}
            )

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="autostack@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"hosted_url:{source_id}"
            )

            detect_conflict(tid, source_id, did, "INV001",
                           {"email": "autostack@example.com"},
                           {"email": "source@example.com"})

            conflict_id = get_unresolved_conflicts(tid)[0]["id"]
            success = resolve_conflict(tid, conflict_id, "keep_autostack")
            assert success

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 0

    def test_resolve_conflict_use_source(self, app, auth_user):
        """Resolve conflict by using source value."""
        tid = auth_user

        with app.app_context():
            _, source_id = create_data_source(
                tid,
                "Test",
                "api",
                {"api_url": "https://api.example.com/debtors"}
            )

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="old@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="D123",
                external_source=f"api:{source_id}"
            )

            detect_conflict(tid, source_id, did, "D123",
                           {"email": "old@example.com"},
                           {"email": "new@example.com"})

            conflict_id = get_unresolved_conflicts(tid)[0]["id"]
            success = resolve_conflict(tid, conflict_id, "use_source")
            assert success

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 0


class TestDataSourcesAPI:
    """Test HTTP endpoints for data source management."""

    def test_preview_local_file(self, client, auth_user, app):
        """Preview local file before connecting."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("Invoice No.,Name,Email\n")
            f.write("INV001,John Doe,john@example.com\n")
            f.write("INV002,Jane Smith,jane@example.com\n")
            temp_path = f.name

        try:
            with app.app_context():
                response = client.post(
                    "/api/data-sources/preview",
                    json={"source_type": "local_file", "config": {"file_path": temp_path}},
                    content_type="application/json"
                )

            assert response.status_code == 200
            data = response.get_json()
            assert "preview" in data
            assert "columns" in data
            assert len(data["preview"]) == 2
        finally:
            os.unlink(temp_path)

    def test_connect_local_file(self, client, auth_user, app):
        """Connect a local file via API."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            with app.app_context():
                response = client.post(
                    "/api/data-sources/connect",
                    json={
                        "name": "My Debtors",
                        "source_type": "local_file",
                        "config": {"file_path": temp_path},
                        "column_mapping": {"email": "Email"}
                    },
                    content_type="application/json"
                )

            assert response.status_code == 201
            data = response.get_json()
            assert "source_id" in data
            assert "pairing_token" in data
            assert len(data["pairing_token"]) == 32
        finally:
            os.unlink(temp_path)

    def test_connect_hosted_url(self, client, auth_user, app):
        """Connect a hosted URL via API."""
        with app.app_context():
            response = client.post(
                "/api/data-sources/connect",
                json={
                    "name": "Online Debtors",
                    "source_type": "hosted_url",
                    "config": {"url": "https://example.com/debtors.csv"},
                    "column_mapping": {"email": "Contact Email"}
                },
                content_type="application/json"
            )

        assert response.status_code == 201
        data = response.get_json()
        assert "source_id" in data
        assert "pairing_token" not in data  # URL sources don't get tokens

    def test_list_sources(self, client, auth_user, app):
        """List all data sources."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            with app.app_context():
                # Create two sources
                client.post(
                    "/api/data-sources/connect",
                    json={
                        "name": "Source 1",
                        "source_type": "local_file",
                        "config": {"file_path": temp_path}
                    },
                    content_type="application/json"
                )

                client.post(
                    "/api/data-sources/connect",
                    json={
                        "name": "Source 2",
                        "source_type": "hosted_url",
                        "config": {"url": "https://example.com/data.csv"}
                    },
                    content_type="application/json"
                )

                response = client.get("/api/data-sources/list")

            assert response.status_code == 200
            data = response.get_json()
            assert len(data["sources"]) == 2
            assert data["conflict_count"] == 0
        finally:
            os.unlink(temp_path)

    def test_disconnect_source(self, client, auth_user, app):
        """Disconnect a data source."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            with app.app_context():
                response = client.post(
                    "/api/data-sources/connect",
                    json={
                        "name": "Temp Source",
                        "source_type": "local_file",
                        "config": {"file_path": temp_path}
                    },
                    content_type="application/json"
                )
                source_id = response.get_json()["source_id"]

                response = client.post(
                    "/api/data-sources/disconnect",
                    json={"source_id": source_id},
                    content_type="application/json"
                )

            assert response.status_code == 200

            # Verify source is disconnected
            response = client.get("/api/data-sources/list")
            data = response.get_json()
            assert len(data["sources"]) == 0
        finally:
            os.unlink(temp_path)
