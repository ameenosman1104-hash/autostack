"""Transactional versioned database migrations with backup support."""
import sqlite3
import os
import shutil
from datetime import datetime
from pathlib import Path


def backup_database(db_path):
    """Create a timestamped backup using SQLite backup API.

    Uses SQLite's official backup API to ensure WAL files are included.
    Verifies restoration before returning backup path.
    """
    if not os.path.exists(db_path):
        return None

    backup_dir = os.path.join(os.path.dirname(db_path), ".backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{Path(db_path).stem}__{timestamp}.db")

    try:
        # Use SQLite backup API for atomic, consistent backup including WAL data
        source = sqlite3.connect(db_path)
        backup = sqlite3.connect(backup_path)

        # Perform backup
        with backup:
            source.backup(backup)

        source.close()
        backup.close()

        # Verify backup integrity
        verify_backup = sqlite3.connect(backup_path)
        result = verify_backup.execute("PRAGMA integrity_check").fetchone()[0]
        verify_backup.close()

        if result != "ok":
            os.remove(backup_path)
            raise RuntimeError(f"Backup verification failed: {result}")

        return backup_path

    except Exception as e:
        # Clean up failed backup
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except:
                pass
        raise RuntimeError(f"Backup failed for {db_path}: {e}")


def get_schema_version(conn):
    """Get current schema version. Returns 0 if migration table doesn't exist."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        return row[0] if row[0] else 0
    except sqlite3.OperationalError:
        return 0


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
    conn.commit()


def migrate_tenant_db(tenant_id, db_path):
    """Apply all pending migrations to a tenant database."""

    # Create backup before any changes (if DB exists and has data)
    backup_path = None
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            has_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0] > 0
            conn.close()
            if has_tables:
                backup_path = backup_database(db_path)
        except:
            pass

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")

        # Initialize base schema if not exists (new databases)
        conn.executescript("""
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
        conn.commit()

        current_version = get_schema_version(conn)

        # Migration 1: Create reminder_dispatch ledger for duplicate prevention
        if current_version < 1:
            conn.execute("BEGIN")
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
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 1 failed: {e}")

        # Migration 2: Add sync fields to debtors for existing databases
        if current_version < 2:
            conn.execute("BEGIN")
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(debtors)").fetchall()}

                if "due_date" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN due_date TEXT DEFAULT NULL")
                if "sync_snapshot" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN sync_snapshot TEXT DEFAULT NULL")
                if "sync_pending" not in cols:
                    conn.execute("ALTER TABLE debtors ADD COLUMN sync_pending INTEGER DEFAULT 0")

                mark_migration_applied(conn, 2, "Add sync fields to debtors")
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 2 failed: {e}")

        # Migration 3: Add missing product columns for existing databases
        if current_version < 3:
            conn.execute("BEGIN")
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
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 3 failed: {e}")

        # Migration 4: Encrypt existing plaintext credentials
        if current_version < 4:
            conn.execute("BEGIN")
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
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 4 failed: {e}")

        # Migration 5: Add gmail_oauth_tokens table for Gmail API integration
        if current_version < 5:
            conn.execute("BEGIN")
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
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Migration 5 failed: {e}")

        conn.close()
        return backup_path

    except Exception as e:
        # Log backup location so user can restore if needed
        error_msg = f"Database migration failed for tenant {tenant_id}. Error: {e}"
        if backup_path:
            error_msg += f"\nBackup saved to: {backup_path}"
        raise RuntimeError(error_msg)


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
