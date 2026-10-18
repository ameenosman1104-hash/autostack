"""Additive supplier invoice safeguards; conflicting legacy records are never deleted."""
from app.db_migrations import get_schema_version, mark_migration_applied


def migrate_supplier_invoices(conn):
    if get_schema_version(conn) >= 21:
        return
    if not conn.in_transaction:
        raise RuntimeError("Supplier migrations require the migration runner write transaction")
    try:
        duplicate = conn.execute("""SELECT MIN(id) FROM supplier_invoices
            GROUP BY lower(trim(supplier_name)), lower(trim(invoice_number))
            HAVING COUNT(*) > 1 LIMIT 1""").fetchone()
        if duplicate:
            raise RuntimeError("Supplier invoice duplicates require manual reconciliation; no records removed")
        conn.execute("""CREATE UNIQUE INDEX idx_supplier_invoices_identity
            ON supplier_invoices(lower(trim(supplier_name)), lower(trim(invoice_number)))""")
        conn.execute("""CREATE UNIQUE INDEX idx_supplier_invoices_supplier_identity
            ON supplier_invoices(supplier_id, lower(trim(invoice_number))) WHERE supplier_id IS NOT NULL""")
        for column, definition in (
            ("extraction_token", "TEXT"), ("extraction_started_at", "REAL"),
            ("extraction_error", "TEXT"), ("extraction_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("extraction_model", "TEXT"), ("extraction_sha256", "TEXT"),
        ):
            conn.execute(f"ALTER TABLE supplier_invoices ADD COLUMN {column} {definition}")
        conn.execute("""CREATE TABLE supplier_invoice_extraction_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_invoice_id INTEGER NOT NULL REFERENCES supplier_invoices(id) ON DELETE CASCADE,
            actor TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Existing foreign key violations require reconciliation")
        mark_migration_applied(conn, 21, "Supplier invoice identity and extraction safeguards")
    except Exception:
        conn.rollback()
        raise


def migrate_supplier_reference(conn):
    """Rebuild only the supplier invoice parent to enforce its supplier foreign key."""
    if get_schema_version(conn) >= 22:
        return
    # The runner disables FK enforcement before taking the write lock.
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Supplier rebuild requires foreign keys disabled by migration runner")
    try:
        if not conn.in_transaction:
            raise RuntimeError("Supplier migrations require the migration runner write transaction")
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='supplier_invoices'").fetchone()[0]
        ddl = ddl.replace("supplier_invoices (", "supplier_invoices_rebuild (", 1)
        ddl = ddl.replace('"supplier_invoices" (', '"supplier_invoices_rebuild" (', 1)
        ddl = ddl.replace("supplier_id INTEGER,", "supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT,", 1)
        if "supplier_invoices_rebuild" not in ddl or "REFERENCES suppliers" not in ddl:
            raise RuntimeError("Unexpected supplier invoice schema; rebuild aborted")
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='supplier_invoices'").fetchone()
        triggers = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='supplier_invoices' AND sql IS NOT NULL")]
        indexes = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='supplier_invoices' AND sql IS NOT NULL")]
        columns = ",".join('"' + row[1] + '"' for row in conn.execute("PRAGMA table_info(supplier_invoices)"))
        conn.execute(ddl)
        conn.execute(f"INSERT INTO supplier_invoices_rebuild ({columns}) SELECT {columns} FROM supplier_invoices")
        conn.execute("DROP TABLE supplier_invoices")
        conn.execute("ALTER TABLE supplier_invoices_rebuild RENAME TO supplier_invoices")
        for sql in indexes + triggers:
            conn.execute(sql)
        if sequence:
            conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='supplier_invoices'", (sequence[0],))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Existing foreign key violations; no invoice records removed")
        mark_migration_applied(conn, 22, "Enforce supplier invoice supplier reference")
    except Exception:
        conn.rollback()
        raise


def migrate_supplier_reviews(conn):
    """Keep editable drafts separate from immutable extraction results."""
    if get_schema_version(conn) >= 23:
        return
    if not conn.in_transaction:
        raise RuntimeError("Supplier migrations require the migration runner write transaction")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_invoice_reviews (
            supplier_invoice_id INTEGER PRIMARY KEY REFERENCES supplier_invoices(id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK(version > 0),
            draft_json TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_invoice_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_invoice_id INTEGER NOT NULL REFERENCES supplier_invoices(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, draft_json TEXT NOT NULL, actor TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(supplier_invoice_id, version)
        )""")
        mark_migration_applied(conn, 23, "Supplier invoice versioned review drafts")
    except Exception:
        conn.rollback()
        raise


def migrate_supplier_confirmations(conn):
    """Atomic processing receipts provide invoice and business-identity idempotency."""
    if get_schema_version(conn) >= 24:
        return
    if not conn.in_transaction:
        raise RuntimeError("Supplier migrations require the migration runner write transaction")
    try:
        conn.execute("""CREATE TABLE supplier_invoice_confirmations (
            supplier_invoice_id INTEGER PRIMARY KEY REFERENCES supplier_invoices(id) ON DELETE RESTRICT,
            identity_key TEXT NOT NULL UNIQUE,
            review_version INTEGER NOT NULL CHECK(review_version > 0),
            snapshot_hash TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        mark_migration_applied(conn, 24, "Supplier invoice atomic confirmation receipts")
    except Exception:
        conn.rollback()
        raise
