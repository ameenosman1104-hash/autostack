"""Transactional versioned database migrations with backup support."""
import sqlite3
import os
import shutil
import json
import tempfile
from datetime import datetime
from pathlib import Path


LATEST_SCHEMA_VERSION = 30
MIGRATION_BUSY_TIMEOUT_SECONDS = 30.0


def backup_database(db_path):
    """Create a timestamped backup using SQLite backup API.

    Uses SQLite's official backup API to ensure WAL files are included.
    Verifies restoration before returning backup path.
    """
    if not os.path.exists(db_path):
        return None

    backup_dir = os.path.join(os.path.dirname(db_path), ".backups")
    os.makedirs(backup_dir, exist_ok=True)

    # Exclusive allocation prevents simultaneous backups from overwriting one another.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fd, backup_path = tempfile.mkstemp(
        prefix=f"{Path(db_path).stem}__{timestamp}_", suffix=".db", dir=backup_dir
    )
    os.close(fd)
    try:
        source = sqlite3.connect(db_path)
        try:
            backup = sqlite3.connect(backup_path)
            try:
                source.backup(backup)
                result = backup.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                backup.close()
        finally:
            source.close()
        if result != "ok":
            raise RuntimeError(f"Backup verification failed: {result}")
        return backup_path
    except Exception as e:
        # Retain even failed artifacts; never remove or replace an existing backup.
        raise RuntimeError(f"Backup failed for {db_path} (retained at {backup_path}): {e}")


def get_schema_version(conn):
    """Get current schema version. Returns 0 if migration table doesn't exist."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        return row[0] if row[0] else 0
    except sqlite3.OperationalError:
        return 0


def _execute_schema_script(conn, script):
    """Execute base DDL without executescript() implicitly committing our lock."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        conn.execute(statement)


def mark_migration_applied(conn, version, name):
    """Record that a migration was applied."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
        (version, name)
    )


QUOTATION_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS quotation_sequence (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        next_number INTEGER NOT NULL CHECK (next_number >= 1)
    )""",
    "INSERT OR IGNORE INTO quotation_sequence (id, next_number) VALUES (1, 1)",
    """CREATE TABLE IF NOT EXISTS quotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quotation_number TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'sent', 'accepted', 'rejected', 'expired', 'converted')),
        converted_invoice_id INTEGER REFERENCES invoices(id),
        customer_id INTEGER REFERENCES customers(id),
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        quote_date TEXT NOT NULL,
        expiry_date TEXT NOT NULL,
        subtotal REAL NOT NULL DEFAULT 0,
        discount_total REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        terms TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        sent_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS quotation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK (item_type IN ('product', 'service', 'custom')),
        product_id INTEGER,
        service_id INTEGER,
        description TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL CHECK (quantity > 0),
        base_price REAL NOT NULL CHECK (base_price >= 0),
        unit_price REAL NOT NULL CHECK (unit_price >= 0),
        discount_pct REAL NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 100),
        line_total REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS quotation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_quotation_items_quote ON quotation_items(quotation_id)",
    "CREATE INDEX IF NOT EXISTS idx_quotation_events_quote ON quotation_events(quotation_id)",
    "CREATE INDEX IF NOT EXISTS idx_quotations_status_expiry ON quotations(status, expiry_date)",
)


JOB_CARD_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        registration TEXT NOT NULL UNIQUE,
        make TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        year INTEGER CHECK (year IS NULL OR (year >= 1950 AND year <= 2100)),
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS job_card_sequence (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        next_number INTEGER NOT NULL CHECK (next_number >= 1)
    )""",
    "INSERT OR IGNORE INTO job_card_sequence (id, next_number) VALUES (1, 1)",
    """CREATE TABLE IF NOT EXISTS job_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_number TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'in_progress', 'ready', 'completed', 'cancelled')),
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        mileage_in INTEGER CHECK (mileage_in IS NULL OR mileage_in >= 0),
        mileage_out INTEGER CHECK (mileage_out IS NULL OR mileage_out >= 0),
        technician TEXT NOT NULL DEFAULT '',
        quotation_id INTEGER REFERENCES quotations(id),
        invoice_id INTEGER REFERENCES invoices(id),
        promised_date TEXT,
        work_performed TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        subtotal REAL NOT NULL DEFAULT 0,
        discount_total REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL DEFAULT 0,
        price_confirmed_by TEXT NOT NULL DEFAULT '',
        price_confirmed_at TEXT,
        created_by TEXT NOT NULL DEFAULT '',
        opened_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS job_card_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_card_id INTEGER NOT NULL REFERENCES job_cards(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK (item_type IN ('product', 'service')),
        product_id INTEGER REFERENCES products(id),
        service_id INTEGER REFERENCES services(id),
        description TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL CHECK (quantity > 0),
        base_price REAL NOT NULL CHECK (base_price >= 0),
        unit_price REAL NOT NULL CHECK (unit_price >= 0),
        discount_pct REAL NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 100),
        line_total REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS job_card_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_card_id INTEGER NOT NULL REFERENCES job_cards(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_cards_customer ON job_cards(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_cards_vehicle ON job_cards(vehicle_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_cards_status ON job_cards(status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_cards_invoice ON job_cards(invoice_id) WHERE invoice_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_cards_quotation ON job_cards(quotation_id) WHERE quotation_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_job_card_items_job ON job_card_items(job_card_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_card_events_job ON job_card_events(job_card_id)",
)


JOB_PRICE_APPROVAL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS job_card_price_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_card_id INTEGER NOT NULL REFERENCES job_cards(id) ON DELETE CASCADE,
        item_position INTEGER NOT NULL,
        description TEXT NOT NULL,
        old_price REAL NOT NULL CHECK (old_price >= 0),
        new_price REAL NOT NULL CHECK (new_price >= 0),
        approved_by TEXT NOT NULL,
        approved_at TEXT NOT NULL DEFAULT (datetime('now')),
        invoice_id INTEGER REFERENCES invoices(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_job_price_approvals_job ON job_card_price_approvals(job_card_id)",
)

TECHNICIAN_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS technicians (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        phone TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_technicians_active ON technicians(is_active, name)",
)


def migrate_tenant_db(tenant_id, db_path):
    """Apply all pending migrations to a tenant database."""
    backup_path = None
    conn = None
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=MIGRATION_BUSY_TIMEOUT_SECONDS)
        # Current databases take neither a write lock nor a backup.
        if get_schema_version(conn) >= LATEST_SCHEMA_VERSION:
            return None

        # Table rebuilds need FK enforcement off before BEGIN; the FK check below still runs before commit.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        # Another worker may have finished the upgrade while this one waited for the lock.
        if get_schema_version(conn) >= LATEST_SCHEMA_VERSION:
            conn.rollback()
            return None
        has_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone() is not None
        if has_tables:
            # The writer lock excludes other writers through backup and commit.
            try:
                backup_path = backup_database(db_path)
            except Exception as e:
                raise RuntimeError("Migration requires a verified backup; no changes made") from e
            if backup_path is None:
                raise RuntimeError("Migration requires a verified backup; no changes made")

        # Initialize base schema if not exists (new databases)
        _execute_schema_script(conn, """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                unit TEXT DEFAULT 'PCS',
                current_stock REAL DEFAULT 0,
                reorder_level REAL DEFAULT 0,
                min_level_manual INTEGER DEFAULT 0,
                max_stock REAL DEFAULT 0,
                last_cost_price REAL DEFAULT 0,
                previous_cost_price REAL DEFAULT 0,
                supplier TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                extra_data TEXT DEFAULT '{}',
                deleted INTEGER DEFAULT 0,
                deleted_at TEXT DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS debtors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                amount_owed REAL DEFAULT 0,
                date_of_purchase TEXT NOT NULL,
                notify_method TEXT DEFAULT 'email',
                reminder_days INTEGER DEFAULT 14,
                notes TEXT DEFAULT '',
                products_owed TEXT DEFAULT '',
                is_paid INTEGER DEFAULT 0,
                last_reminded TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'DUE',
                reminders_paused_until TEXT DEFAULT NULL,
                last_payment_date TEXT DEFAULT NULL,
                total_paid_to_date REAL DEFAULT 0,
                next_reminder_date TEXT DEFAULT NULL,
                reminder_mode TEXT DEFAULT 'default',
                reminder_interval_days INTEGER DEFAULT 28,
                external_key TEXT DEFAULT NULL,
                external_source TEXT DEFAULT NULL,
                last_synced_at TEXT DEFAULT NULL,
                sync_status TEXT DEFAULT 'manual'
            );
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debtor_id INTEGER NOT NULL,
                amount_paid REAL DEFAULT 0,
                payment_date TEXT NOT NULL,
                payment_method TEXT DEFAULT 'cash',
                notes TEXT DEFAULT '',
                recorded_by TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS reminder_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debtor_id INTEGER NOT NULL,
                reminder_date TEXT NOT NULL,
                method TEXT DEFAULT 'email',
                status TEXT DEFAULT 'sent',
                message_preview TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sync_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debtor_id INTEGER,
                external_key TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                sync_source TEXT DEFAULT 'manual',
                sync_action TEXT DEFAULT 'update',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                po_number TEXT UNIQUE NOT NULL,
                order_date TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'DRAFT',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS purchase_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                po_id INTEGER NOT NULL,
                product_code TEXT,
                product_name TEXT,
                unit TEXT DEFAULT 'PCS',
                current_stock REAL DEFAULT 0,
                reorder_level REAL DEFAULT 0,
                order_quantity REAL DEFAULT 0,
                previous_cost_price REAL DEFAULT 0,
                estimated_total REAL DEFAULT 0,
                FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_code TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                qty_sold REAL DEFAULT 0,
                sale_price REAL DEFAULT 0,
                total_amount REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS stock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_code TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                change_type TEXT DEFAULT '',
                qty_before REAL DEFAULT 0,
                qty_after REAL DEFAULT 0,
                change_by REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                address TEXT DEFAULT '',
                lead_time_days INTEGER DEFAULT 0,
                payment_terms TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_type TEXT,
                recipient TEXT,
                message TEXT,
                status TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)

        current_version = get_schema_version(conn)

        # Migration 1: Create reminder_dispatch ledger for duplicate prevention
        if current_version < 1:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS reminder_dispatch (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        debtor_id INTEGER NOT NULL,
                        scheduled_date TEXT NOT NULL,
                        sent_at TEXT DEFAULT NULL,
                        status TEXT DEFAULT 'pending',
                        error_message TEXT DEFAULT NULL,
                        created_at TEXT DEFAULT (datetime('now')),
                        UNIQUE(debtor_id, scheduled_date),
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
                    )
                """)
                mark_migration_applied(conn, 1, "Create reminder_dispatch ledger")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 1 failed: {e}")

        # Migration 2: Add sync fields to debtors for existing databases
        if current_version < 2:
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(debtors)").fetchall()}

                if "due_date" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN due_date TEXT DEFAULT NULL")
                if "sync_snapshot" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN sync_snapshot TEXT DEFAULT NULL")
                if "sync_pending" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN sync_pending INTEGER DEFAULT 0")

                mark_migration_applied(conn, 2, "Add sync fields to debtors")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 2 failed: {e}")

        # Migration 3: Add missing product columns for existing databases
        if current_version < 3:
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}

                # Add all missing product columns
                if "updated_at" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))")
                if "extra_data" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN extra_data TEXT DEFAULT '{}'")
                if "deleted" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN deleted INTEGER DEFAULT 0")
                if "deleted_at" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN deleted_at TEXT DEFAULT NULL")
                if "min_level_manual" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN min_level_manual INTEGER DEFAULT 0")

                mark_migration_applied(conn, 3, "Add product columns for existing databases")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 3 failed: {e}")

        # Migration 4: Encrypt existing plaintext credentials
        if current_version < 4:
            try:
                from app.credential_store import encrypt_value, should_encrypt_key, is_encrypted

                # Get all settings that need encryption
                rows = conn.execute("SELECT key, value FROM settings").fetchall()
                for row in rows:
                    key = row[0]
                    value = row[1]

                    if should_encrypt_key(key) and value and not is_encrypted(value):
                        encrypted = encrypt_value(value)
                        conn.execute("UPDATE settings SET value=? WHERE key=?", (encrypted, key))

                mark_migration_applied(conn, 4, "Encrypt plaintext credentials")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 4 failed: {e}")

        # Migration 5: Add gmail_oauth_tokens table for Gmail API integration
        if current_version < 5:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gmail_oauth_tokens (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        authorized_email TEXT NOT NULL UNIQUE,
                        access_token TEXT NOT NULL,
                        refresh_token TEXT,
                        expires_at INTEGER,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                mark_migration_applied(conn, 5, "Add gmail_oauth_tokens table")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 5 failed: {e}")

        # Migration 6: Add live_excel_connections table for Excel sync
        if current_version < 6:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS live_excel_connections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_name TEXT NOT NULL,
                        workbook_id TEXT NOT NULL,
                        worksheet_name TEXT NOT NULL,
                        table_name TEXT,
                        access_token TEXT NOT NULL,
                        refresh_token TEXT,
                        expires_at INTEGER,
                        graph_user_id TEXT NOT NULL,
                        column_mapping TEXT,
                        unique_key_field TEXT,
                        last_sync_at TEXT,
                        last_sync_status TEXT DEFAULT 'pending',
                        last_sync_error TEXT,
                        sync_interval_seconds INTEGER DEFAULT 15,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                mark_migration_applied(conn, 6, "Add live_excel_connections table")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 6 failed: {e}")

        # Migration 7: Add local file connection tables for device pairing and conflict tracking
        if current_version < 7:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS local_file_connections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        worksheet_name TEXT,
                        pairing_token_hash TEXT NOT NULL UNIQUE,
                        device_id TEXT,
                        device_name TEXT,
                        status TEXT DEFAULT 'active',
                        column_mapping TEXT DEFAULT '{}',
                        unique_key_field TEXT DEFAULT 'Invoice No.',
                        last_sync_at TEXT,
                        last_sync_status TEXT DEFAULT 'pending',
                        last_sync_error TEXT,
                        file_mtime_ns INTEGER,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS local_file_changes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_id INTEGER NOT NULL,
                        debtor_id INTEGER NOT NULL,
                        change_type TEXT NOT NULL,
                        old_values TEXT,
                        new_values TEXT,
                        applied_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (connection_id) REFERENCES local_file_connections(id) ON DELETE CASCADE,
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS local_file_conflicts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_id INTEGER NOT NULL,
                        debtor_id INTEGER NOT NULL,
                        invoice_id TEXT NOT NULL,
                        autostack_values TEXT NOT NULL,
                        source_values TEXT NOT NULL,
                        resolution TEXT,
                        resolved_at TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (connection_id) REFERENCES local_file_connections(id) ON DELETE CASCADE,
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
                    )
                """)
                mark_migration_applied(conn, 7, "Add local file connection and conflict tables")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 7 failed: {e}")

        # Migration 8: Unified data source connections for local, hosted URL, and API sources
        if current_version < 8:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS data_source_connections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        status TEXT DEFAULT 'active',
                        config TEXT NOT NULL,
                        column_mapping TEXT DEFAULT '{}',
                        unique_key_field TEXT DEFAULT 'Invoice No.',
                        last_sync_at TEXT,
                        last_sync_status TEXT DEFAULT 'pending',
                        last_sync_error TEXT,
                        sync_interval_seconds INTEGER DEFAULT 15,
                        pairing_token_hash TEXT,
                        device_id TEXT,
                        device_name TEXT,
                        file_path TEXT,
                        worksheet_name TEXT,
                        file_mtime_ns INTEGER,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS data_source_changes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_id INTEGER NOT NULL,
                        debtor_id INTEGER NOT NULL,
                        change_type TEXT NOT NULL,
                        old_values TEXT,
                        new_values TEXT,
                        applied_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (connection_id) REFERENCES data_source_connections(id) ON DELETE CASCADE,
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS data_source_conflicts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_id INTEGER NOT NULL,
                        debtor_id INTEGER NOT NULL,
                        invoice_id TEXT NOT NULL,
                        autostack_values TEXT NOT NULL,
                        source_values TEXT NOT NULL,
                        resolution TEXT,
                        resolved_at TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (connection_id) REFERENCES data_source_connections(id) ON DELETE CASCADE,
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
                    )
                """)
                mark_migration_applied(conn, 8, "Add unified data source connections for all three types")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 8 failed: {e}")

        # Migration 9: Add destination field to separate Stock/Debtors connections
        if get_schema_version(conn) < 9:
            try:
                conn.execute("PRAGMA foreign_keys=OFF")

                # Add destination column to data_source_connections
                try:
                    conn.execute("""
                        ALTER TABLE data_source_connections
                        ADD COLUMN destination TEXT DEFAULT 'debtors'
                    """)
                except sqlite3.OperationalError:
                    # Column may already exist from previous migration attempt
                    pass

                # Ensure matching_identifier column exists for both stock and debtors
                try:
                    conn.execute("""
                        ALTER TABLE data_source_connections
                        ADD COLUMN matching_identifier TEXT DEFAULT 'auto'
                    """)
                except sqlite3.OperationalError:
                    pass

                conn.execute("PRAGMA foreign_keys=ON")
                mark_migration_applied(conn, 9, "Add destination field for Stock/Debtors separation")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 9 failed: {e}")

        # Migration 10: Add columns for credential encryption and file handle persistence
        if get_schema_version(conn) < 10:
            try:
                conn.execute("PRAGMA foreign_keys=OFF")

                # Add credential encryption and file handle columns
                try:
                    conn.execute("""
                        ALTER TABLE data_source_connections
                        ADD COLUMN credentials_encrypted TEXT
                    """)
                except sqlite3.OperationalError:
                    pass  # Column may already exist

                try:
                    conn.execute("""
                        ALTER TABLE data_source_connections
                        ADD COLUMN file_handle_id TEXT
                    """)
                except sqlite3.OperationalError:
                    pass  # Column may already exist

                conn.execute("PRAGMA foreign_keys=ON")
                mark_migration_applied(conn, 10, "Add credentials and file handle support for saved connections")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 10 failed: {e}")

        # Migration 11: Create customers table for POS
        if get_schema_version(conn) < 11:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS customers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        phone TEXT DEFAULT '',
                        email TEXT DEFAULT '',
                        vehicle_registration TEXT DEFAULT '',
                        notes TEXT DEFAULT '',
                        customer_type TEXT DEFAULT 'regular',
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email)")
                mark_migration_applied(conn, 11, "Create customers table for POS")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 11 failed: {e}")

        # Migration 12: Create invoices table for POS transactions
        if get_schema_version(conn) < 12:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS invoices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        invoice_number TEXT UNIQUE NOT NULL,
                        idempotency_key TEXT UNIQUE NOT NULL,
                        customer_id INTEGER,
                        debtor_id INTEGER,
                        sale_date TEXT NOT NULL,
                        status TEXT DEFAULT 'completed',
                        subtotal REAL DEFAULT 0,
                        discount REAL DEFAULT 0,
                        tax REAL DEFAULT 0,
                        total REAL DEFAULT 0,
                        payment_method TEXT,
                        payment_status TEXT DEFAULT 'completed',
                        notes TEXT DEFAULT '',
                        created_by INTEGER,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (customer_id) REFERENCES customers(id),
                        FOREIGN KEY (debtor_id) REFERENCES debtors(id)
                    )
                """)
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number)")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_idempotency ON invoices(idempotency_key)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_debtor ON invoices(debtor_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(sale_date)")
                mark_migration_applied(conn, 12, "Create invoices table for POS transactions")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 12 failed: {e}")

        # Migration 13: Create invoice_items table for line items
        if get_schema_version(conn) < 13:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS invoice_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        invoice_id INTEGER NOT NULL,
                        item_type TEXT,
                        product_id INTEGER,
                        service_id INTEGER,
                        quantity REAL DEFAULT 0,
                        unit_price REAL DEFAULT 0,
                        total REAL DEFAULT 0,
                        discount REAL DEFAULT 0,
                        FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                        FOREIGN KEY (product_id) REFERENCES products(id),
                        FOREIGN KEY (service_id) REFERENCES services(id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_sale ON invoice_items(invoice_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_product ON invoice_items(product_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_service ON invoice_items(service_id)")
                mark_migration_applied(conn, 13, "Create invoice_items table for line items")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 13 failed: {e}")

        # Migration 14: Create services table for POS
        if get_schema_version(conn) < 14:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS services (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        default_price REAL DEFAULT 0,
                        category TEXT DEFAULT '',
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_services_active ON services(is_active)")
                mark_migration_applied(conn, 14, "Create services table for POS services")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 14 failed: {e}")

        # Migration 15: Add tyre-specific columns to products
        if get_schema_version(conn) < 15:
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}

                if "condition" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN condition TEXT DEFAULT NULL")
                if "brand" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN brand TEXT DEFAULT ''")
                if "tyre_size" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN tyre_size TEXT DEFAULT ''")
                if "pattern" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN pattern TEXT DEFAULT ''")

                mark_migration_applied(conn, 15, "Add tyre-specific columns to products")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 15 failed: {e}")

        # Migration 16: Add customer_id to debtors
        if get_schema_version(conn) < 16:
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(debtors)").fetchall()}

                if "customer_id" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN customer_id INTEGER DEFAULT NULL")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_debtors_customer ON debtors(customer_id)")

                mark_migration_applied(conn, 16, "Add customer_id to debtors table")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 16 failed: {e}")

        # Migration 17: Add selling_price column to products + backfill from extra_data
        if get_schema_version(conn) < 17:
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}

                if "selling_price" not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN selling_price REAL DEFAULT NULL")

                # Backfill: extract valid selling_price from extra_data JSON
                products = conn.execute("SELECT id, extra_data FROM products WHERE selling_price IS NULL").fetchall()
                for product_id, extra_data_str in products:
                    try:
                        if extra_data_str:
                            extra_data = json.loads(extra_data_str)
                            price = extra_data.get('selling_price')

                            # Only backfill if: parseable, numeric, >= 0
                            if price is not None:
                                price_float = float(price)
                                if price_float >= 0:
                                    conn.execute(
                                        "UPDATE products SET selling_price = ? WHERE id = ?",
                                        (price_float, product_id)
                                    )
                    except (json.JSONDecodeError, ValueError, TypeError):
                        # Leave as NULL if malformed or invalid
                        pass

                mark_migration_applied(conn, 17, "Add selling_price column + backfill from extra_data")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 17 failed: {e}")

        # Migration 18: Create invoice_sequences table for atomic invoice numbering
        if get_schema_version(conn) < 18:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS invoice_sequences (
                        tid INTEGER PRIMARY KEY,
                        next_number INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                mark_migration_applied(conn, 18, "Create invoice_sequences table for POS")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 18 failed: {e}")

        # Migration 19: Create supplier_invoices table for purchase invoices
        if get_schema_version(conn) < 19:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS supplier_invoices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        supplier_id INTEGER,
                        supplier_name TEXT NOT NULL,
                        invoice_number TEXT NOT NULL,
                        invoice_date TEXT,
                        upload_date TEXT DEFAULT (datetime('now')),
                        file_name TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        file_size INTEGER DEFAULT 0,
                        mime_type TEXT DEFAULT 'application/octet-stream',
                        extracted_json TEXT DEFAULT '{}',
                        status TEXT DEFAULT 'uploaded',
                        confirmation_date TEXT,
                        created_by TEXT NOT NULL,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_invoices_supplier ON supplier_invoices(supplier_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_invoices_invoice_number ON supplier_invoices(invoice_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_invoices_status ON supplier_invoices(status)")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_supplier_invoices_unique ON supplier_invoices(supplier_id, invoice_number)")
                mark_migration_applied(conn, 19, "Create supplier_invoices table for purchase invoices")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 19 failed: {e}")

        # Migration 20: Create supplier_invoice_items table for line items
        if get_schema_version(conn) < 20:
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS supplier_invoice_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        supplier_invoice_id INTEGER NOT NULL,
                        product_code TEXT,
                        product_description TEXT,
                        brand TEXT,
                        tyre_size TEXT,
                        pattern_model TEXT,
                        quantity REAL DEFAULT 0,
                        unit_cost REAL DEFAULT 0,
                        line_total REAL DEFAULT 0,
                        matched_product_id INTEGER,
                        extraction_confidence TEXT DEFAULT 'high',
                        requires_review INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (supplier_invoice_id) REFERENCES supplier_invoices(id) ON DELETE CASCADE,
                        FOREIGN KEY (matched_product_id) REFERENCES products(id) ON DELETE SET NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_invoice_items_invoice ON supplier_invoice_items(supplier_invoice_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_invoice_items_product ON supplier_invoice_items(matched_product_id)")
                mark_migration_applied(conn, 20, "Create supplier_invoice_items table for line items")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 20 failed: {e}")

        from app.services.supplier_invoice_schema import migrate_supplier_invoices, migrate_supplier_reference, migrate_supplier_reviews, migrate_supplier_confirmations
        migrate_supplier_invoices(conn)
        migrate_supplier_reference(conn)
        migrate_supplier_reviews(conn)
        migrate_supplier_confirmations(conn)
        # Migration 25: Quotations, line items, sequential numbering and audit events
        if get_schema_version(conn) < 25:
            try:
                for statement in QUOTATION_SCHEMA_STATEMENTS:
                    conn.execute(statement)
                mark_migration_applied(conn, 25, "Create quotation tables")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 25 failed: {e}")

        # Migration 26: Vehicles and job cards (workshop). No stock movement occurs here.
        if get_schema_version(conn) < 26:
            try:
                for statement in JOB_CARD_SCHEMA_STATEMENTS:
                    conn.execute(statement)
                mark_migration_applied(conn, 26, "Create vehicles and job card tables")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 26 failed: {e}")

        # Migration 27: customer complaint on job cards (additive column, existing rows keep ''):
        if get_schema_version(conn) < 27:
            try:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(job_cards)").fetchall()}
                if "customer_complaint" not in columns:
                    conn.execute("ALTER TABLE job_cards ADD COLUMN customer_complaint TEXT NOT NULL DEFAULT ''")
                mark_migration_applied(conn, 27, "Add customer complaint to job cards")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 27 failed: {e}")

        # Migration 28: append-only price approval history for job cards. Additive; nothing existing changes.
        if get_schema_version(conn) < 28:
            try:
                for statement in JOB_PRICE_APPROVAL_STATEMENTS:
                    conn.execute(statement)
                mark_migration_applied(conn, 28, "Create job card price approval history")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 28 failed: {e}")

        # Migration 29: technicians, and an optional link from each job card. Existing job cards keep their
        # free-text technician name and get NULL technician_id, so nothing already stored is rewritten.
        if get_schema_version(conn) < 29:
            try:
                for statement in TECHNICIAN_STATEMENTS:
                    conn.execute(statement)
                columns = {r[1] for r in conn.execute("PRAGMA table_info(job_cards)").fetchall()}
                if "technician_id" not in columns:
                    conn.execute("ALTER TABLE job_cards ADD COLUMN technician_id INTEGER REFERENCES technicians(id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_job_cards_technician ON job_cards(technician_id, status)")
                mark_migration_applied(conn, 29, "Create technicians and link job cards to them")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 29 failed: {e}")

        # Migration 30: time and idempotency for money records. Additive columns only. Existing rows keep NULL
        # here: their timestamps are not guessed or rewritten.
        if get_schema_version(conn) < 30:
            try:
                def add_missing(table, column, definition):
                    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                add_missing("invoices", "business_date", "TEXT")
                add_missing("invoices", "recorded_at_utc", "TEXT")
                add_missing("payment_history", "business_date", "TEXT")
                add_missing("payment_history", "recorded_at_utc", "TEXT")
                add_missing("payment_history", "idempotency_key", "TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_business_date ON invoices(business_date)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_history_business_date ON payment_history(business_date)")
                conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_history_idempotency
                                ON payment_history(idempotency_key) WHERE idempotency_key IS NOT NULL""")
                mark_migration_applied(conn, 30, "Add business date, UTC record time and payment idempotency")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 30 failed: {e}")

        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Foreign key violations; migration rolled back")
        conn.commit()
        return backup_path

    except Exception as e:
        if conn is not None:
            conn.rollback()
        # Log backup location so user can restore if needed
        error_msg = f"Database migration failed for tenant {tenant_id}. Error: {e}"
        if backup_path:
            error_msg += f"\nBackup saved to: {backup_path}"
        raise RuntimeError(error_msg)
    finally:
        if conn is not None:
            conn.close()




def verify_backup_restoration(backup_path, verification_dir):
    """Verify that a backup can be restored by opening it and checking integrity."""
    if not os.path.exists(backup_path):
        raise RuntimeError(f"Backup file not found: {backup_path}")

    # Create verification directory if needed
    os.makedirs(verification_dir, exist_ok=True)

    # Copy backup to verification location
    verification_path = os.path.join(verification_dir, f"verify_{os.path.basename(backup_path)}")
    shutil.copy2(backup_path, verification_path)

    try:
        # Test opening and checking integrity
        conn = sqlite3.connect(verification_path)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()

        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")

        return verification_path
    except Exception as e:
        if os.path.exists(verification_path):
            os.remove(verification_path)
        raise RuntimeError(f"Failed to verify backup restoration: {e}")
    finally:
        # Clean up verification copy
        if os.path.exists(verification_path):
            os.remove(verification_path)
