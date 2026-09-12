"""Local file connection pairing, sync management, and conflict resolution."""
import hashlib
import secrets
import json
import time
from datetime import datetime
from pathlib import Path


def generate_pairing_token():
    """Generate a secure, user-friendly pairing token (32 random bytes)."""
    return secrets.token_hex(16)


def hash_pairing_token(token):
    """Hash a pairing token for storage. One-way operation."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_local_connection(tid, name, file_path, worksheet_name=None, column_mapping=None, unique_key_field="Invoice No."):
    """Create a new local file connection with a generated pairing token."""
    from ..tenant_db import get_conn
    from ..main_db import get_conn as get_main_conn

    pairing_token = generate_pairing_token()
    token_hash = hash_pairing_token(pairing_token)

    conn = get_conn(tid)
    try:
        conn.execute("""
            INSERT INTO local_file_connections
            (name, file_path, worksheet_name, pairing_token_hash, column_mapping, unique_key_field, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
        """, (
            name,
            file_path,
            worksheet_name,
            token_hash,
            json.dumps(column_mapping or {}),
            unique_key_field
        ))
        conn.commit()

        # Get the connection ID that was just created
        result = conn.execute("SELECT last_insert_rowid()").fetchone()
        connection_id = result[0] if result else None
        conn.close()

        # Register token in main database for tenant lookup
        if connection_id:
            main_conn = get_main_conn()
            try:
                main_conn.execute("""
                    INSERT INTO local_file_pairing_tokens
                    (tenant_id, connection_id, token_hash)
                    VALUES (?, ?, ?)
                """, (tid, connection_id, token_hash))
                main_conn.commit()
                main_conn.close()
            except Exception as e:
                main_conn.close()
                # Log but don't fail if token registration fails
                import sys
                print(f"Warning: Failed to register token in main DB: {e}", file=sys.stderr)

        return pairing_token
    except Exception as e:
        conn.close()
        raise


def lookup_tenant_by_token(pairing_token):
    """Look up tenant and connection ID from a pairing token. Returns (tid, connection_id) or (None, None)."""
    from ..main_db import get_conn as get_main_conn

    token_hash = hash_pairing_token(pairing_token)

    main_conn = get_main_conn()
    try:
        row = main_conn.execute("""
            SELECT tenant_id, connection_id
            FROM local_file_pairing_tokens
            WHERE token_hash = ?
            LIMIT 1
        """, (token_hash,)).fetchone()
        main_conn.close()

        if row:
            return row[0], row[1]
        return None, None
    except:
        main_conn.close()
        return None, None


def get_connection_by_token(tid, pairing_token):
    """Verify a pairing token and return connection metadata (if valid)."""
    from ..tenant_db import get_conn

    token_hash = hash_pairing_token(pairing_token)
    conn = get_conn(tid)
    try:
        row = conn.execute("""
            SELECT id, name, file_path, worksheet_name, column_mapping, unique_key_field,
                   last_sync_at, last_sync_status, last_sync_error, status
            FROM local_file_connections
            WHERE pairing_token_hash = ? AND status = 'active'
            LIMIT 1
        """, (token_hash,)).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "id": row[0],
            "name": row[1],
            "file_path": row[2],
            "worksheet_name": row[3],
            "column_mapping": json.loads(row[4] or "{}"),
            "unique_key_field": row[5],
            "last_sync_at": row[6],
            "last_sync_status": row[7],
            "last_sync_error": row[8],
            "status": row[9]
        }
    except Exception as e:
        conn.close()
        raise


def list_connections(tid):
    """List all active local file connections for a tenant."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        rows = conn.execute("""
            SELECT id, name, file_path, worksheet_name, status, last_sync_at, last_sync_status
            FROM local_file_connections
            WHERE status = 'active'
            ORDER BY updated_at DESC
        """).fetchall()
        conn.close()

        return [{
            "id": row[0],
            "name": row[1],
            "file_path": row[2],
            "worksheet_name": row[3],
            "status": row[4],
            "last_sync_at": row[5],
            "last_sync_status": row[6]
        } for row in rows]
    except:
        conn.close()
        return []


def revoke_connection(tid, connection_id):
    """Revoke a connection (soft delete). Device cannot sync after this."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE local_file_connections
            SET status = 'revoked', updated_at = datetime('now')
            WHERE id = ?
        """, (connection_id,))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def update_sync_status(tid, connection_id, status, error=None):
    """Update the last sync status and timestamp."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE local_file_connections
            SET last_sync_at = datetime('now'),
                last_sync_status = ?,
                last_sync_error = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (status, error, connection_id))
        conn.commit()
        conn.close()
    except:
        conn.close()


def record_change(tid, connection_id, debtor_id, change_type, old_values=None, new_values=None):
    """Audit log: record what changed during sync."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            INSERT INTO local_file_changes
            (connection_id, debtor_id, change_type, old_values, new_values)
            VALUES (?, ?, ?, ?, ?)
        """, (
            connection_id,
            debtor_id,
            change_type,
            json.dumps(old_values or {}),
            json.dumps(new_values or {})
        ))
        conn.commit()
        conn.close()
    except:
        conn.close()


def detect_conflict(tid, connection_id, debtor_id, invoice_id, autostack_values, source_values):
    """Flag a conflict for user resolution."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        # Check if conflict already exists for this invoice
        existing = conn.execute("""
            SELECT id FROM local_file_conflicts
            WHERE connection_id = ? AND invoice_id = ? AND resolution IS NULL
            LIMIT 1
        """, (connection_id, invoice_id)).fetchone()

        if existing:
            # Update existing conflict
            conn.execute("""
                UPDATE local_file_conflicts
                SET autostack_values = ?, source_values = ?, created_at = datetime('now')
                WHERE id = ?
            """, (
                json.dumps(autostack_values),
                json.dumps(source_values),
                existing[0]
            ))
        else:
            # Create new conflict
            conn.execute("""
                INSERT INTO local_file_conflicts
                (connection_id, debtor_id, invoice_id, autostack_values, source_values)
                VALUES (?, ?, ?, ?, ?)
            """, (
                connection_id,
                debtor_id,
                invoice_id,
                json.dumps(autostack_values),
                json.dumps(source_values)
            ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.close()
        return False


def resolve_conflict(tid, conflict_id, resolution):
    """Mark a conflict as resolved. resolution = 'keep_autostack' or 'use_source'."""
    from ..tenant_db import get_conn

    if resolution not in ('keep_autostack', 'use_source'):
        raise ValueError("Invalid resolution")

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE local_file_conflicts
            SET resolution = ?, resolved_at = datetime('now')
            WHERE id = ?
        """, (resolution, conflict_id))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def get_unresolved_conflicts(tid):
    """Get all conflicts awaiting user resolution."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        rows = conn.execute("""
            SELECT id, connection_id, debtor_id, invoice_id, autostack_values, source_values
            FROM local_file_conflicts
            WHERE resolution IS NULL
            ORDER BY created_at DESC
        """).fetchall()
        conn.close()

        return [{
            "id": row[0],
            "connection_id": row[1],
            "debtor_id": row[2],
            "invoice_id": row[3],
            "autostack_values": json.loads(row[4]),
            "source_values": json.loads(row[5])
        } for row in rows]
    except:
        conn.close()
        return []
