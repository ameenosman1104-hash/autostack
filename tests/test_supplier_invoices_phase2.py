"""Mocked AI integration and persistent-storage/database safety regressions."""
import json
import os
import sqlite3
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_supplier_invoices_phase1 import app, client, auth_headers
from app.tenant_db import get_conn
from app.db_migrations import migrate_tenant_db
from app.services import supplier_invoice_extraction as extraction


def pdf_bytes(pages=2):
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


PDF = pdf_bytes()
PNG = b"\x89PNG\r\n\x1a\nfixture"
JPG = b"\xff\xd8\xfffixture"


def document(client, ext="pdf", content=PDF, number="INV1"):
    response = client.post("/suppliers/invoices/upload", data={
        "supplier_name": "Supplier", "invoice_number": number,
        "file": (BytesIO(content), "invoice." + ext)})
    assert response.status_code == 201, response.json
    return response.json["invoice_id"]


def output():
    item = {"product_code": "T1", "product_description": "Tyre", "brand": "Brand",
            "tyre_size": "205/55R16", "pattern_model": "Touring", "quantity": 2,
            "unit_cost": 100, "line_total": 200, "vat": 30, "page": 1, "confidence": "high"}
    return {"supplier_name": "Supplier", "invoice_number": "INV1", "invoice_date": "2026-10-14",
            "subtotal": 400, "vat": 60, "invoice_total": 460, "currency": "ZAR",
            "page_count": 2, "line_totals_include_vat": False,
            "items": [item, {**item, "product_code": "T2", "page": 2}]}


@pytest.fixture
def ai(monkeypatch):
    parse = Mock(return_value=SimpleNamespace(status="completed", output_parsed=output()))
    factory = Mock(return_value=SimpleNamespace(responses=SimpleNamespace(parse=parse), close=Mock()))
    monkeypatch.setattr(extraction.openai, "OpenAI", factory)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    return parse


@pytest.mark.parametrize("ext,content,kind", [("pdf", PDF, "input_file"), ("png", PNG, "input_image"), ("jpg", JPG, "input_image")])
def test_formats_multi_products_and_no_inventory_changes(client, auth_headers, ai, ext, content, kind):
    conn = get_conn(10)
    conn.execute("INSERT INTO products (code,name,current_stock) VALUES ('ORIGINAL','Existing tyre',9)")
    conn.commit()
    before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
              for table in ("products", "stock_history", "invoices", "debtors", "customers")}
    conn.close()
    iid = document(client, ext, content)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 200, response.json
    assert response.json["extraction"]["review_flags"] == []
    assert response.json["extraction"]["requires_review"] is True
    args = ai.call_args.kwargs
    assert args["input"][0]["content"][0]["type"] == kind
    assert args["text_format"] is extraction.InvoiceExtraction
    assert args["store"] is False and args["max_output_tokens"] == 6000
    conn = get_conn(10)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items WHERE matched_product_id IS NOT NULL").fetchone()[0] == 0
    invoice = conn.execute("SELECT * FROM supplier_invoices WHERE id=?", (iid,)).fetchone()
    assert invoice["status"] == "extracted" and invoice["confirmation_date"] is None
    assert invoice["extraction_sha256"] and invoice["extraction_model"]
    for table, rows in before.items():
        assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows
    conn.close()
    assert client.get(f"/suppliers/invoices/{iid}/extraction").json["extraction"] == response.json["extraction"]
    assert client.post(f"/suppliers/invoices/{iid}/extract").json["cached"] is True
    assert ai.call_count == 1


@pytest.mark.parametrize("change,flag", [
    ({"invoice_total": 900}, "inconsistent:invoice_total"),
    ({"subtotal": 500, "invoice_total": 560}, "inconsistent:line_totals_sum"),
    ({"supplier_name": None}, "missing:supplier_name"),
    ({"invoice_date": "14/??/2026"}, "invalid:invoice_date"),
    ({"vat": None}, "missing:vat"),
    ({"items": []}, "missing:items"),
])
def test_review_flags_preserve_printed_values(client, auth_headers, ai, change, flag):
    data = {**output(), **change}
    ai.return_value.output_parsed = data
    iid = document(client)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 200
    assert flag in response.json["extraction"]["review_flags"]
    for key, value in change.items():
        assert response.json["extraction"][key] == value


def test_missing_fields_and_uncertain_items(client, auth_headers, ai):
    ai.return_value.output_parsed = {"items": [{"product_description": "Unclear tyre", "warnings": ["Blurred price"]}]}
    iid = document(client)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 200
    result = response.json["extraction"]
    assert result["invoice_total"] is None and result["items"][0]["unit_cost"] is None
    assert "items[0].uncertain" in result["review_flags"]
    conn = get_conn(10)
    assert conn.execute("SELECT quantity FROM supplier_invoice_items").fetchone()[0] is None
    conn.close()


@pytest.mark.parametrize("bad", ["not JSON", {"items": "not a list"}, {"invoice_total": "NaN"},
    {"items": [{"quantity": -2}]}, {"unexpected": True}, {"items": [{"unit_cost": "abc"}]}])
def test_malformed_response_retry(client, auth_headers, ai, bad):
    ai.return_value.output_parsed = bad
    iid = document(client)
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 502
    conn = get_conn(10)
    assert conn.execute("SELECT status FROM supplier_invoices").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 0
    conn.close()
    ai.return_value.output_parsed = output()
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 200
    conn = get_conn(10)
    assert conn.execute("SELECT extraction_attempts FROM supplier_invoices").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_extraction_events").fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize("status,parsed", [("incomplete", output()), ("completed", None)])
def test_incomplete_or_refused_response(client, auth_headers, ai, status, parsed):
    ai.return_value = SimpleNamespace(status=status, output_parsed=parsed)
    iid = document(client)
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 502


def test_provider_timeout_redacted_and_retryable(client, auth_headers, ai):
    ai.side_effect = TimeoutError("SECRET_PROVIDER_CONTENT")
    iid = document(client)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 502 and "SECRET" not in response.text
    conn = get_conn(10)
    assert "SECRET" not in conn.execute("SELECT extraction_error FROM supplier_invoices").fetchone()[0]
    conn.close()


def test_unknown_and_cross_tenant_never_call_ai(client, auth_headers, ai):
    iid = document(client)
    assert client.post("/suppliers/invoices/999/extract").status_code == 404
    from app.main_db import get_conn as main_conn
    conn = main_conn()
    conn.execute("INSERT INTO tenants (id,business_name,username,password_hash) VALUES (20,'Other','other','hash')")
    conn.commit(); conn.close()
    with client.session_transaction() as session:
        session["_user_id"] = "20"
    assert client.post(f"/suppliers/invoices/{iid}/extract", headers={"X-Tenant-ID": "10"}).status_code == 404
    assert client.get(f"/suppliers/invoices/{iid}/extraction").status_code == 404
    ai.assert_not_called()


def test_extract_authentication_and_csrf(client, app, ai):
    assert client.post("/suppliers/invoices/1/extract").status_code == 302
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/suppliers/invoices/1/extract").status_code == 400
    ai.assert_not_called()


def test_lease_blocks_concurrent_calls_and_recovers_stale_claim(client, auth_headers, ai):
    iid = document(client)
    conn = get_conn(10)
    conn.execute("UPDATE supplier_invoices SET status='extracting',extraction_started_at=?,extraction_token='old'", (time.time(),))
    conn.commit(); conn.close()
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 409
    ai.assert_not_called()
    conn = get_conn(10)
    conn.execute("UPDATE supplier_invoices SET extraction_started_at=0")
    conn.commit(); conn.close()
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 200
    assert ai.call_count == 1


def test_stale_completion_cannot_overwrite_new_claim(client, auth_headers, ai):
    iid = document(client)
    def supersede(**kwargs):
        conn = get_conn(10)
        conn.execute("UPDATE supplier_invoices SET extraction_token='new-owner'")
        conn.commit(); conn.close()
        return SimpleNamespace(status="completed", output_parsed=output())
    ai.side_effect = supersede
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 409
    conn = get_conn(10)
    assert conn.execute("SELECT extraction_token FROM supplier_invoices").fetchone()[0] == "new-owner"
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("status", ["reviewed", "processed"])
def test_later_states_are_not_modified(client, auth_headers, ai, status):
    iid = document(client)
    conn = get_conn(10)
    conn.execute("UPDATE supplier_invoices SET status=?,extracted_json=?", (status, '{"approved":true}'))
    conn.commit(); conn.close()
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.json["status"] == status and response.json["extraction"] == {"approved": True}
    ai.assert_not_called()


def test_path_outside_tenant_storage_never_sent(client, auth_headers, ai, tmp_path):
    iid = document(client)
    other = tmp_path / "outside.pdf"
    other.write_bytes(PDF)
    conn = get_conn(10)
    conn.execute("UPDATE supplier_invoices SET file_path=?", (str(other),))
    conn.commit(); conn.close()
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 502
    ai.assert_not_called()


def test_atomic_persistence_on_item_failure(client, auth_headers, ai):
    iid = document(client)
    conn = get_conn(10)
    conn.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON supplier_invoice_items
        WHEN NEW.product_code='T2' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
    conn.commit(); conn.close()
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 502
    conn = get_conn(10)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 0
    assert conn.execute("SELECT extracted_json FROM supplier_invoices").fetchone()[0] == '{}'
    conn.close()


def insert_invoice(conn, supplier="Supplier", number="INV", sid=None):
    return conn.execute("""INSERT INTO supplier_invoices
        (supplier_id,supplier_name,invoice_number,file_name,file_path,created_by)
        VALUES (?,?,?,'i.pdf','/not-used','test')""", (sid, supplier, number)).lastrowid


@pytest.mark.parametrize("sid", [None, 1])
def test_database_duplicates_including_null_supplier(app, sid):
    conn = get_conn(10)
    conn.execute("INSERT INTO suppliers (id,name) VALUES (1,'Supplier')")
    insert_invoice(conn, sid=sid)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        insert_invoice(conn, " supplier ", " inv ", sid)
    conn.rollback()
    conn.execute("UPDATE supplier_invoices SET status='failed'")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        insert_invoice(conn, sid=sid)
    conn.close()


def test_foreign_keys_enforced_and_cascade(app):
    conn = get_conn(10)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        insert_invoice(conn, sid=999)
    conn.rollback()
    iid = insert_invoice(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO supplier_invoice_items (supplier_invoice_id) VALUES (999)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO supplier_invoice_items (supplier_invoice_id,matched_product_id) VALUES (?,999)", (iid,))
    conn.execute("INSERT INTO supplier_invoice_items (supplier_invoice_id) VALUES (?)", (iid,))
    conn.execute("DELETE FROM supplier_invoices WHERE id=?", (iid,))
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 0
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()


def legacy_db(path):
    migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX idx_supplier_invoices_identity")
    conn.execute("DROP INDEX idx_supplier_invoices_supplier_identity")
    conn.execute("DROP TABLE supplier_invoice_extraction_events")
    for col in ("extraction_token", "extraction_started_at", "extraction_error", "extraction_attempts", "extraction_model", "extraction_sha256"):
        conn.execute(f"ALTER TABLE supplier_invoices DROP COLUMN {col}")
    for table in ("supplier_invoice_confirmations", "supplier_invoice_review_events", "supplier_invoice_reviews"):
        conn.execute(f"DROP TABLE {table}")
    # Restore the exact Phase 1 parent shape (no supplier FK), not only its version.
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='supplier_invoices'").fetchone()[0]
    ddl = ddl.replace('"supplier_invoices"', 'supplier_invoices_oldshape', 1)
    ddl = ddl.replace('supplier_invoices (', 'supplier_invoices_oldshape (', 1)
    ddl = ddl.replace("supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT,", "supplier_id INTEGER,")
    indexes = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='supplier_invoices' AND sql IS NOT NULL")]
    conn.execute(ddl)
    conn.execute("DROP TABLE supplier_invoices")
    conn.execute("ALTER TABLE supplier_invoices_oldshape RENAME TO supplier_invoices")
    for sql in indexes:
        conn.execute(sql)
    conn.execute("DELETE FROM schema_migrations WHERE version>=21")
    conn.commit()
    return conn


def test_existing_database_upgrade_preserves_records_and_is_repeatable(tmp_path):
    path = tmp_path / "legacy.db"
    conn = legacy_db(path)
    iid = insert_invoice(conn)
    conn.execute("INSERT INTO supplier_invoice_items (supplier_invoice_id,product_description) VALUES (?,'Legacy tyre')", (iid,))
    conn.execute("INSERT INTO products (code,name,current_stock) VALUES ('OLD','Old',7)")
    conn.commit(); conn.close()
    migrate_tenant_db(10, str(path))
    migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0] == 1
    assert conn.execute("SELECT product_description FROM supplier_invoice_items").fetchone()[0] == 'Legacy tyre'
    assert conn.execute("SELECT current_stock FROM products").fetchone()[0] == 7
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 28
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()


def test_legacy_duplicates_fail_without_deleting_data(tmp_path):
    path = tmp_path / "duplicates.db"
    conn = legacy_db(path)
    insert_invoice(conn)
    insert_invoice(conn)
    conn.commit(); conn.close()
    with pytest.raises(RuntimeError, match="manual reconciliation"):
        migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0] == 2
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 20
    conn.close()


@pytest.mark.parametrize("configured,mounted,inside", [(False,False,True),(True,False,True),(True,True,False)])
def test_production_storage_fails_closed(app, client, auth_headers, monkeypatch, tmp_path, configured, mounted, inside):
    monkeypatch.setenv("RENDER", "true")  # overrides the test/development fallback
    mount = tmp_path / "volume"
    monkeypatch.setenv("AUTOSTACK_INVOICE_MOUNT", str(mount))
    if configured:
        monkeypatch.setenv("AUTOSTACK_INVOICE_DIR", str(mount / "invoices" if inside else tmp_path / "ephemeral"))
    monkeypatch.setattr(os.path, "ismount", lambda path: mounted)
    response = client.post("/suppliers/invoices/upload", data={
        "supplier_name": "Supplier", "invoice_number": "INV",
        "file": (BytesIO(PDF), "invoice.pdf")})
    assert response.status_code == 503
    assert not (tmp_path / "ephemeral").exists()


def test_verified_production_volume_accepts_storage(app, client, auth_headers, monkeypatch, tmp_path):
    mount = tmp_path / "volume"
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("AUTOSTACK_INVOICE_MOUNT", str(mount))
    monkeypatch.setenv("AUTOSTACK_INVOICE_DIR", str(mount / "invoices"))
    monkeypatch.setattr(os.path, "ismount", lambda path: path == mount)
    assert document(client) > 0


def test_known_supplier_duplicate_uses_id_even_if_name_changes(app):
    conn = get_conn(10)
    conn.execute("INSERT INTO suppliers (id,name) VALUES (1,'Supplier')")
    insert_invoice(conn, sid=1)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        insert_invoice(conn, supplier="Renamed Supplier", number=" inv ", sid=1)
    conn.close()


def test_existing_orphan_supplier_blocks_rebuild_without_data_loss(tmp_path):
    path = tmp_path / "orphan.db"
    conn = legacy_db(path)
    insert_invoice(conn, sid=999)
    conn.commit(); conn.close()
    with pytest.raises(RuntimeError, match="foreign key violations"):
        migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT supplier_id FROM supplier_invoices").fetchone()[0] == 999
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='supplier_invoices_rebuild'").fetchone()[0] == 0
    conn.close()


def test_line_total_uncertainty_is_flagged_without_recalculation(client, auth_headers, ai):
    data = output()
    data["items"][0]["line_total"] = 250
    ai.return_value.output_parsed = data
    iid = document(client)
    result = client.post(f"/suppliers/invoices/{iid}/extract").json["extraction"]
    assert result["items"][0]["line_total"] == 250
    assert "items[0].inconsistent:line_total" in result["review_flags"]


def test_database_identity_changes_are_protected(app):
    conn = get_conn(10)
    insert_invoice(conn, number="ONE")
    iid = insert_invoice(conn, number="TWO")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE supplier_invoices SET invoice_number=' one ' WHERE id=?", (iid,))
    conn.close()


@pytest.mark.parametrize("content", [b"%PDF-1.4 malformed", pdf_bytes(21)])
def test_invalid_or_excessive_pdf_never_calls_ai(client, auth_headers, ai, content):
    iid = document(client, content=content)
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 502
    ai.assert_not_called()


def test_extracted_identity_disagreement_is_flagged(client, auth_headers, ai):
    data = output()
    data["invoice_number"] = "OTHER"
    ai.return_value.output_parsed = data
    iid = document(client)
    result = client.post(f"/suppliers/invoices/{iid}/extract").json["extraction"]
    assert "inconsistent:uploaded_invoice_number" in result["review_flags"]
    assert result["invoice_number"] == "OTHER"
    conn = get_conn(10)
    assert conn.execute("SELECT invoice_number FROM supplier_invoices").fetchone()[0] == "INV1"
    conn.close()


def test_upgrade_requires_successful_backup(tmp_path, monkeypatch):
    path = tmp_path / "backup_failure.db"
    conn = legacy_db(path)
    insert_invoice(conn)
    conn.commit(); conn.close()
    def fail_backup(path):
        raise OSError("test backup unavailable")
    monkeypatch.setattr("app.db_migrations.backup_database", fail_backup)
    with pytest.raises(RuntimeError, match="verified backup"):
        migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0] == 1
    conn.close()


def provider_429(code):
    import httpx2
    import openai
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(429, request=request)
    return openai.RateLimitError("provider text SECRET_BILLING", response=response,
                                 body={"message": "SECRET_BILLING", "type": "insufficient_quota", "code": code})


@pytest.mark.parametrize("code", ["credit_balance_exhausted", "insufficient_quota"])
def test_exhausted_credits_return_clear_retryable_error(client, auth_headers, ai, code):
    ai.side_effect = provider_429(code)
    iid = document(client)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 503
    assert "no remaining credits" in response.json["error"] and "saved" in response.json["error"]
    assert "SECRET" not in response.text and code not in response.text
    conn = get_conn(10)
    row = conn.execute("SELECT status, extraction_error, file_path FROM supplier_invoices WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "failed" and row["extraction_error"] == extraction.CREDITS_EXHAUSTED
    assert Path(row["file_path"]).is_file()
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_items").fetchone()[0] == 0
    conn.close()
    ai.side_effect = None
    ai.return_value = SimpleNamespace(status="completed", output_parsed=output())
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code == 200


def test_other_rate_limits_keep_generic_retryable_error(client, auth_headers, ai):
    ai.side_effect = provider_429("rate_limit_exceeded")
    iid = document(client)
    response = client.post(f"/suppliers/invoices/{iid}/extract")
    assert response.status_code == 502
    assert response.json["error"] == extraction.GENERIC_FAILURE
    conn = get_conn(10)
    assert conn.execute("SELECT extraction_error FROM supplier_invoices").fetchone()[0] == extraction.GENERIC_FAILURE
    conn.close()


def test_upgrade_preserves_autoincrement_after_deleted_invoice(tmp_path):
    path = tmp_path / "sequence.db"
    conn = legacy_db(path)
    insert_invoice(conn)
    deleted = insert_invoice(conn, number="DELETED")
    conn.execute("DELETE FROM supplier_invoices WHERE id=?", (deleted,))
    conn.commit(); conn.close()
    migrate_tenant_db(10, str(path))
    conn = sqlite3.connect(path)
    assert insert_invoice(conn, number="NEW") > deleted
    conn.close()
