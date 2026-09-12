"""Tests for local file connection pairing and synchronization."""
import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from app import create_app
from app.main_db import get_conn as get_main_conn
from app.tenant_db import get_conn
from app.services.local_file_sync import (
    generate_pairing_token,
    hash_pairing_token,
    create_local_connection,
    get_connection_by_token,
    lookup_tenant_by_token,
    revoke_connection,
    update_sync_status,
    record_change,
    detect_conflict,
    resolve_conflict,
    get_unresolved_conflicts,
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
        assert not err, f"Failed to create tenant: {err}"

        # Log in
        response = client.post(
            "/login",
            data={"username": "testuser", "password": "password123"},
            follow_redirects=True
        )
        assert response.status_code == 200

    return tid


class TestPairingTokens:
    """Test pairing token generation and validation."""

    def test_generate_token(self):
        """Pairing token is 32-char hex string."""
        token = generate_pairing_token()
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_uniqueness(self):
        """Each token is unique."""
        tokens = [generate_pairing_token() for _ in range(10)]
        assert len(set(tokens)) == 10, "Tokens should be unique"

    def test_token_hash_deterministic(self):
        """Hash of same token is always identical."""
        token = generate_pairing_token()
        hash1 = hash_pairing_token(token)
        hash2 = hash_pairing_token(token)
        assert hash1 == hash2

    def test_token_hash_different_tokens(self):
        """Different tokens produce different hashes."""
        token1 = generate_pairing_token()
        token2 = generate_pairing_token()
        assert hash_pairing_token(token1) != hash_pairing_token(token2)

    def test_token_hash_is_sha256(self):
        """Hash uses SHA256 (64 hex chars)."""
        token = generate_pairing_token()
        token_hash = hash_pairing_token(token)
        assert len(token_hash) == 64
        assert all(c in "0123456789abcdef" for c in token_hash)


class TestLocalFileConnection:
    """Test local file connection creation and management."""

    def test_create_connection(self, app, auth_user):
        """Create a new local file connection."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(
                tid,
                name="Test File",
                file_path="/tmp/test.csv",
                worksheet_name=None,
                column_mapping={"name": "Name", "email": "Email"},
                unique_key_field="Invoice No."
            )

            assert token
            assert len(token) == 32

            # Verify connection exists in tenant DB
            conn = get_conn(tid)
            row = conn.execute(
                "SELECT * FROM local_file_connections WHERE name=?",
                ("Test File",)
            ).fetchone()
            conn.close()

            assert row
            assert row["file_path"] == "/tmp/test.csv"
            assert row["status"] == "active"

    def test_connection_registered_in_main_db(self, app, auth_user):
        """Pairing token is registered in main database."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(
                tid,
                name="Test File",
                file_path="/tmp/test.csv",
                column_mapping={"email": "Email"}
            )

            # Verify token is in main database
            main_conn = get_main_conn()
            token_hash = hash_pairing_token(token)
            row = main_conn.execute(
                "SELECT * FROM local_file_pairing_tokens WHERE token_hash=?",
                (token_hash,)
            ).fetchone()
            main_conn.close()

            assert row
            assert row["tenant_id"] == tid

    def test_lookup_tenant_by_token(self, app, auth_user):
        """Look up tenant from pairing token."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(
                tid,
                name="Test File",
                file_path="/tmp/test.csv"
            )

            looked_up_tid, connection_id = lookup_tenant_by_token(token)
            assert looked_up_tid == tid
            assert connection_id is not None

    def test_lookup_invalid_token(self, app):
        """Invalid token returns None."""
        with app.app_context():
            tid, conn_id = lookup_tenant_by_token("invalid-token-12345678901234567890")
            assert tid is None
            assert conn_id is None

    def test_get_connection_by_token(self, app, auth_user):
        """Retrieve connection metadata using token."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(
                tid,
                name="Test File",
                file_path="/tmp/test.xlsx",
                worksheet_name="Sheet1",
                column_mapping={"email": "Email Address"},
                unique_key_field="Invoice No."
            )

            conn_data = get_connection_by_token(tid, token)
            assert conn_data
            assert conn_data["name"] == "Test File"
            assert conn_data["worksheet_name"] == "Sheet1"
            assert conn_data["column_mapping"]["email"] == "Email Address"

    def test_revoke_connection(self, app, auth_user):
        """Revoke a connection (soft delete)."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")

            looked_up_tid, conn_id = lookup_tenant_by_token(token)
            success = revoke_connection(tid, conn_id)
            assert success

            # After revocation, lookup returns None
            looked_up_tid, conn_id = lookup_tenant_by_token(token)
            assert looked_up_tid is None

    def test_update_sync_status(self, app, auth_user):
        """Update sync status in database."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")
            _, conn_id = lookup_tenant_by_token(token)

            update_sync_status(tid, conn_id, "success")

            conn = get_conn(tid)
            row = conn.execute(
                "SELECT * FROM local_file_connections WHERE id=?",
                (conn_id,)
            ).fetchone()
            conn.close()

            assert row["last_sync_status"] == "success"
            assert row["last_sync_at"] is not None


class TestChangeTracking:
    """Test audit trail and change logging."""

    def test_record_change(self, app, auth_user):
        """Record a change in audit log."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")
            _, conn_id = lookup_tenant_by_token(token)

            # Create a test debtor first
            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="test@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"local_file:{conn_id}"
            )

            record_change(
                tid,
                conn_id,
                did,
                "update",
                old_values={"email": "old@example.com"},
                new_values={"email": "new@example.com"}
            )

            conn = get_conn(tid)
            row = conn.execute(
                "SELECT * FROM local_file_changes WHERE debtor_id=?",
                (did,)
            ).fetchone()
            conn.close()

            assert row
            assert row["change_type"] == "update"
            old = json.loads(row["old_values"])
            new = json.loads(row["new_values"])
            assert old["email"] == "old@example.com"
            assert new["email"] == "new@example.com"


class TestConflictDetection:
    """Test conflict detection and resolution."""

    def test_detect_conflict(self, app, auth_user):
        """Flag a conflict for user resolution."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")
            _, conn_id = lookup_tenant_by_token(token)

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="test@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"local_file:{conn_id}"
            )

            detect_conflict(
                tid,
                conn_id,
                did,
                "INV001",
                autostack_values={"email": "autostack@example.com"},
                source_values={"email": "source@example.com"}
            )

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 1
            assert conflicts[0]["invoice_id"] == "INV001"
            assert conflicts[0]["resolution"] is None

    def test_resolve_conflict_keep_autostack(self, app, auth_user):
        """Resolve conflict by keeping AutoStack value."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")
            _, conn_id = lookup_tenant_by_token(token)

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="test@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"local_file:{conn_id}"
            )

            detect_conflict(
                tid,
                conn_id,
                did,
                "INV001",
                autostack_values={"email": "autostack@example.com"},
                source_values={"email": "source@example.com"}
            )

            conflict_id = get_unresolved_conflicts(tid)[0]["id"]
            success = resolve_conflict(tid, conflict_id, "keep_autostack")
            assert success

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 0

    def test_resolve_conflict_use_source(self, app, auth_user):
        """Resolve conflict by using source value."""
        tid = auth_user

        with app.app_context():
            token = create_local_connection(tid, "Test File", "/tmp/test.csv")
            _, conn_id = lookup_tenant_by_token(token)

            from app.tenant_db import add_debtor
            did = add_debtor(
                tid,
                name="Test Debtor",
                email="test@example.com",
                amount_owed=1000,
                date_of_purchase="2026-01-01",
                external_key="INV001",
                external_source=f"local_file:{conn_id}"
            )

            detect_conflict(
                tid,
                conn_id,
                did,
                "INV001",
                autostack_values={"email": "autostack@example.com"},
                source_values={"email": "source@example.com"}
            )

            conflict_id = get_unresolved_conflicts(tid)[0]["id"]
            success = resolve_conflict(tid, conflict_id, "use_source")
            assert success

            conflicts = get_unresolved_conflicts(tid)
            assert len(conflicts) == 0


class TestLocalFileSyncAPI:
    """Test HTTP endpoints for local file sync."""

    def test_pair_device_requires_auth(self, client):
        """Pair endpoint requires authentication."""
        response = client.post(
            "/api/local-file/pair",
            json={"name": "Test", "file_path": "/tmp/test.csv"},
            content_type="application/json"
        )
        assert response.status_code in (401, 302)  # Redirect to login or 401

    def test_preview_file(self, client, auth_user, app):
        """Preview file endpoint returns column info."""
        # Create a temporary test file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("Invoice No.,Name,Email\n")
            f.write("INV001,John Doe,john@example.com\n")
            f.write("INV002,Jane Smith,jane@example.com\n")
            temp_path = f.name

        try:
            with app.app_context():
                response = client.post(
                    "/api/local-file/preview",
                    json={"file_path": temp_path},
                    content_type="application/json"
                )

            assert response.status_code == 200
            data = response.get_json()
            assert "preview" in data
            assert "columns" in data
            assert "total_rows" in data
            assert len(data["preview"]) <= 5
        finally:
            os.unlink(temp_path)

    def test_pair_device_success(self, client, auth_user, app):
        """Successfully pair a new device."""
        with app.app_context():
            response = client.post(
                "/api/local-file/pair",
                json={
                    "name": "Test Device",
                    "file_path": "/tmp/test.csv",
                    "column_mapping": {"email": "Email"},
                    "unique_key_field": "Invoice No."
                },
                content_type="application/json"
            )

            assert response.status_code == 201
            data = response.get_json()
            assert "pairing_token" in data
            assert len(data["pairing_token"]) == 32

    def test_upload_requires_token(self, client, app):
        """Upload endpoint requires pairing token."""
        with app.app_context():
            response = client.post(
                "/api/local-file/upload",
                json={"snapshot": []},
                content_type="application/json"
            )

            assert response.status_code == 401
            data = response.get_json()
            assert "pairing token" in data["error"].lower()

    def test_status_endpoint(self, client, auth_user, app):
        """Status endpoint returns connection list."""
        with app.app_context():
            response = client.get("/api/local-file/status")

            assert response.status_code == 200
            data = response.get_json()
            assert "connections" in data
            assert "conflicts" in data
            assert "conflict_count" in data
