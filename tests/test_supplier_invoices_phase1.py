"""Phase 1 tests: Supplier invoice upload, storage, and tenant isolation."""

import pytest
import os
import sqlite3
from pathlib import Path
from io import BytesIO
import tempfile
import shutil

from app import create_app
from app.db_migrations import migrate_tenant_db
from app.tenant_db import get_conn


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Isolate all state before app creation; never start external sync workers."""
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSTACK_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("AUTOSTACK_INVOICE_DIR", raising=False)
    import app.main_db as main_db
    monkeypatch.setattr(main_db, "MAIN_DB", str(tmp_path / "main.db"))
    monkeypatch.setattr("app.services.auto_sync.start_background_sync", lambda app: None)
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("""INSERT INTO tenants (id, business_name, username, password_hash)
                    VALUES (10, 'Test Shop', 'testuser', 'not-a-real-password')""")
    conn.commit()
    conn.close()
    with client.session_transaction() as session:
        session["_user_id"] = "10"
        session["_fresh"] = True
    return {"X-Tenant-ID": "10"}


class TestDatabaseMigrations:
    """Test database migrations for supplier invoices."""

    def test_migration_19_creates_supplier_invoices_table(self, app):
        """Migration 19 creates supplier_invoices table with correct schema."""
        with app.app_context():
            data_dir = os.environ.get("AUTOSTACK_DATA_DIR", "data")
            db_path = os.path.join(data_dir, "tenants", "test_1.db")

            # Run migrations
            migrate_tenant_db(1, db_path)

            # Verify table exists
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='supplier_invoices'"
            )
            assert cursor.fetchone() is not None, "supplier_invoices table not created"

            # Verify required columns
            cursor = conn.execute("PRAGMA table_info(supplier_invoices)")
            columns = {row[1] for row in cursor.fetchall()}
            required_cols = {
                'id', 'supplier_id', 'supplier_name', 'invoice_number', 'invoice_date',
                'upload_date', 'file_name', 'file_path', 'file_size', 'mime_type',
                'extracted_json', 'status', 'created_by', 'created_at'
            }
            assert required_cols.issubset(columns), f"Missing columns: {required_cols - columns}"

            # Verify status values
            cursor = conn.execute("SELECT DISTINCT status FROM supplier_invoices")
            assert cursor.fetchone() is None or cursor.fetchone()[0] in ['uploaded', 'extracted', 'reviewed', 'processed', 'failed']

            # Verify unique constraint on (supplier_id, invoice_number)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_supplier_invoices_unique'"
            )
            assert cursor.fetchone() is not None, "Unique index not created"

            conn.close()

    def test_migration_20_creates_supplier_invoice_items_table(self, app):
        """Migration 20 creates supplier_invoice_items table with foreign keys."""
        with app.app_context():
            data_dir = os.environ.get("AUTOSTACK_DATA_DIR", "data")
            db_path = os.path.join(data_dir, "tenants", "test_2.db")

            migrate_tenant_db(1, db_path)

            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='supplier_invoice_items'"
            )
            assert cursor.fetchone() is not None, "supplier_invoice_items table not created"

            # Verify foreign key to supplier_invoices
            cursor = conn.execute("PRAGMA foreign_key_list(supplier_invoice_items)")
            fks = {row[2] for row in cursor.fetchall()}
            assert 'supplier_invoices' in fks, "Foreign key to supplier_invoices not created"

            # Verify foreign key to products
            assert 'products' in fks, "Foreign key to products not created"

            conn.close()


class TestFileUploadValidation:
    """Test file upload validation and security."""

    def test_upload_requires_authentication(self, client):
        """Upload endpoint requires login."""
        response = client.post(
            "/suppliers/invoices/upload",
            data={"supplier_name": "Test", "invoice_number": "INV001"}
        )
        assert response.status_code in [302, 401], "Should require authentication"

    def test_upload_rejects_missing_supplier_name(self, client, auth_headers):
        """Upload rejects missing supplier name."""
        pdf_data = b'%PDF-1.4\n%test'

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "invoice_number": "INV001",
                "file": (BytesIO(pdf_data), "test.pdf")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "Supplier name" in response.get_json()["error"]

    def test_upload_rejects_invalid_file_type(self, client, auth_headers):
        """Upload rejects non-PDF/image files."""
        exe_data = b'MZ\x90\x00'  # EXE header

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "supplier_name": "Test Supplier",
                "invoice_number": "INV001",
                "file": (BytesIO(exe_data), "test.exe")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "Invalid file type" in response.get_json()["error"]

    def test_upload_rejects_oversized_file(self, client, auth_headers):
        """Upload rejects files exceeding size limit."""
        large_pdf = b'%PDF-1.4\n' + (b'x' * (11 * 1024 * 1024))

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "supplier_name": "Test Supplier",
                "invoice_number": "INV001",
                "file": (BytesIO(large_pdf), "large.pdf")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 413

    def test_upload_rejects_invalid_pdf_magic(self, client, auth_headers):
        """Upload rejects files with invalid PDF magic bytes."""
        fake_pdf = b'FAKE\x00\x00' + (b'x' * 1000)

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "supplier_name": "Test Supplier",
                "invoice_number": "INV001",
                "file": (BytesIO(fake_pdf), "fake.pdf")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "not a valid PDF" in response.get_json()["error"]

    def test_upload_rejects_invalid_jpeg_magic(self, client, auth_headers):
        """Upload rejects files claiming to be JPEG but aren't."""
        fake_jpeg = b'FAKE\x00\x00' + (b'x' * 1000)

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "supplier_name": "Test Supplier",
                "invoice_number": "INV001",
                "file": (BytesIO(fake_jpeg), "fake.jpg")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "not a valid JPEG" in response.get_json()["error"]

    def test_upload_rejects_invalid_png_magic(self, client, auth_headers):
        """Upload rejects files claiming to be PNG but aren't."""
        fake_png = b'FAKE\x00\x00' + (b'x' * 1000)

        response = client.post(
            "/suppliers/invoices/upload",
            data={
                "supplier_name": "Test Supplier",
                "invoice_number": "INV001",
                "file": (BytesIO(fake_png), "fake.png")
            },
            headers=auth_headers,
            content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "not a valid PNG" in response.get_json()["error"]


class TestFileUploadStorage:
    """Test file storage and handling."""

    def test_upload_succeeds_with_valid_pdf(self, client, auth_headers, app):
        """Valid PDF upload succeeds and stores file."""
        pdf_data = b'%PDF-1.4\ntest content'

        with app.app_context():
            response = client.post(
                "/suppliers/invoices/upload",
                data={
                    "supplier_name": "Test Supplier",
                    "invoice_number": "INV001",
                    "invoice_date": "2026-10-14",
                    "file": (BytesIO(pdf_data), "invoice.pdf")
                },
                headers=auth_headers,
                content_type="multipart/form-data"
            )

        assert response.status_code == 201
        data = response.get_json()
        assert data["success"] is True
        assert data["invoice_id"] > 0
        assert data["supplier_name"] == "Test Supplier"
        assert data["status"] == "uploaded"

    def test_upload_generates_safe_filename(self, client, auth_headers, app):
        """Upload generates safe server-side filename."""
        pdf_data = b'%PDF-1.4\ntest content'

        with app.app_context():
            response = client.post(
                "/suppliers/invoices/upload",
                data={
                    "supplier_name": "Test Supplier",
                    "invoice_number": "INV001",
                    "file": (BytesIO(pdf_data), "../../malicious/name.pdf")
                },
                headers=auth_headers,
                content_type="multipart/form-data"
            )

        assert response.status_code == 201
        # Verify file stored with safe name (not original path)
        invoice_id = response.get_json()["invoice_id"]

        with app.app_context():
            tenant_id = int(auth_headers["X-Tenant-ID"])
            conn = get_conn(tenant_id)
            file_path = conn.execute(
                "SELECT file_path FROM supplier_invoices WHERE id = ?",
                (invoice_id,)
            ).fetchone()[0]
            conn.close()

            # Safe filename should not contain path separators
            assert ".." not in file_path
            assert "malicious" not in file_path
            assert file_path.endswith(".pdf")


class TestDuplicatePrevention:
    """Test duplicate invoice prevention."""

    def test_upload_rejects_duplicate_invoice(self, client, auth_headers, app):
        """Upload rejects duplicate (same supplier + invoice_number)."""
        pdf_data = b'%PDF-1.4\ntest content'

        with app.app_context():
            # First upload
            response1 = client.post(
                "/suppliers/invoices/upload",
                data={
                    "supplier_name": "Test Supplier",
                    "invoice_number": "INV001",
                    "file": (BytesIO(pdf_data), "invoice1.pdf")
                },
                headers=auth_headers,
                content_type="multipart/form-data"
            )
            assert response1.status_code == 201

            # Second upload (duplicate)
            response2 = client.post(
                "/suppliers/invoices/upload",
                data={
                    "supplier_name": "Test Supplier",
                    "invoice_number": "INV001",
                    "file": (BytesIO(pdf_data), "invoice2.pdf")
                },
                headers=auth_headers,
                content_type="multipart/form-data"
            )

            assert response2.status_code == 409
            assert "already uploaded" in response2.get_json()["error"]


class TestTenantIsolation:
    """Test tenant isolation in invoice storage and access."""

    def test_upload_isolates_by_tenant(self, app, client, auth_headers):
        response = client.post("/suppliers/invoices/upload", data={
            "supplier_name": "Supplier A", "invoice_number": "INV001",
            "file": (BytesIO(b"%PDF-1.4 test"), "invoice.pdf")})
        assert response.status_code == 201
        conn = get_conn(20)
        assert conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0] == 0
        conn.close()
        from app.main_db import get_conn as main_conn
        conn = main_conn()
        conn.execute("INSERT INTO tenants (id,business_name,username,password_hash) VALUES (20,'Other','other','hash')")
        conn.commit()
        conn.close()
        with client.session_transaction() as session:
            session["_user_id"] = "20"
        assert client.get(f"/suppliers/invoices/{response.json['invoice_id']}",
                          headers={"X-Tenant-ID": "10"}).status_code == 404


class TestStorageConfiguration:
    """Test invoice storage configuration and failover."""

    def test_upload_fails_without_storage(self, app, client, auth_headers, monkeypatch):
        monkeypatch.setattr("app.routes.supplier_invoices._verify_storage_location", lambda path: False)
        response = client.post("/suppliers/invoices/upload", data={
            "supplier_name": "Test Supplier", "invoice_number": "INV001",
            "file": (BytesIO(b"%PDF-1.4 test"), "invoice.pdf")})
        assert response.status_code == 503
        assert "not available" in response.json["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
