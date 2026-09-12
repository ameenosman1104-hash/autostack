"""Unified data source management for local files, hosted URLs, and APIs."""
import json
import hashlib
import secrets
import time
from datetime import datetime
from pathlib import Path


def generate_pairing_token():
    """Generate 32-byte secure token for local file devices."""
    return secrets.token_hex(16)


def hash_pairing_token(token):
    """Hash pairing token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def validate_url_config(config):
    """Validate hosted URL configuration."""
    url = config.get("url", "").strip()
    if not url:
        raise ValueError("URL is required")
    if not url.lower().startswith("https://"):
        raise ValueError("URL must use HTTPS (not HTTP)")
    if len(url) > 2048:
        raise ValueError("URL is too long")
    return True


def validate_api_config(config):
    """Validate API configuration."""
    api_url = config.get("api_url", "").strip()
    if not api_url:
        raise ValueError("API URL is required")
    if not api_url.lower().startswith("https://"):
        raise ValueError("API URL must use HTTPS")

    # Optional: auth, pagination, webhook
    auth_type = config.get("auth_type", "")
    if auth_type and auth_type not in ("bearer", "api_key", "basic"):
        raise ValueError(f"Unsupported auth type: {auth_type}")

    return True


def validate_local_file_config(config):
    """Validate local file configuration."""
    file_path = config.get("file_path", "").strip()
    if not file_path:
        raise ValueError("File path is required")

    path_obj = Path(file_path)
    if not path_obj.suffix.lower() in (".csv", ".xlsx"):
        raise ValueError("File must be CSV or XLSX format")

    return True


def create_data_source(tid, name, source_type, config, column_mapping=None, unique_key_field="Invoice No."):
    """Create a new data source connection (local, hosted, or API)."""
    from ..tenant_db import get_conn
    from ..main_db import get_conn as get_main_conn

    if source_type == "local_file":
        validate_local_file_config(config)
        pairing_token = generate_pairing_token()
        token_hash = hash_pairing_token(pairing_token)
    elif source_type == "hosted_url":
        validate_url_config(config)
        pairing_token = None
        token_hash = None
    elif source_type == "api":
        validate_api_config(config)
        pairing_token = None
        token_hash = None
    else:
        raise ValueError(f"Unsupported source type: {source_type}")

    conn = get_conn(tid)
    try:
        conn.execute("""
            INSERT INTO data_source_connections
            (name, source_type, config, column_mapping, unique_key_field,
             pairing_token_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
        """, (
            name,
            source_type,
            json.dumps(config),
            json.dumps(column_mapping or {}),
            unique_key_field,
            token_hash
        ))
        conn.commit()

        result = conn.execute("SELECT last_insert_rowid()").fetchone()
        connection_id = result[0] if result else None
        conn.close()

        # Register pairing token if applicable
        if pairing_token and connection_id:
            main_conn = get_main_conn()
            try:
                main_conn.execute("""
                    INSERT INTO local_file_pairing_tokens
                    (tenant_id, connection_id, token_hash)
                    VALUES (?, ?, ?)
                """, (tid, connection_id, token_hash))
                main_conn.commit()
                main_conn.close()
            except:
                main_conn.close()

        return pairing_token, connection_id
    except Exception as e:
        conn.close()
        raise


def list_data_sources(tid):
    """List all active data source connections."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        rows = conn.execute("""
            SELECT id, name, source_type, status, last_sync_at,
                   last_sync_status, last_sync_error
            FROM data_source_connections
            WHERE status = 'active'
            ORDER BY updated_at DESC
        """).fetchall()
        conn.close()

        return [{
            "id": row[0],
            "name": row[1],
            "source_type": row[2],
            "status": row[3],
            "last_sync_at": row[4],
            "last_sync_status": row[5],
            "last_sync_error": row[6]
        } for row in rows]
    except:
        conn.close()
        return []


def get_data_source(tid, source_id):
    """Get full data source configuration."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        row = conn.execute("""
            SELECT * FROM data_source_connections WHERE id = ? AND status = 'active'
        """, (source_id,)).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "id": row[0],
            "name": row[1],
            "source_type": row[2],
            "status": row[3],
            "config": json.loads(row[4] or "{}"),
            "column_mapping": json.loads(row[5] or "{}"),
            "unique_key_field": row[6],
            "last_sync_at": row[7],
            "last_sync_status": row[8],
            "last_sync_error": row[9],
            "sync_interval_seconds": row[10],
            "file_path": row[12],
            "worksheet_name": row[13]
        }
    except:
        conn.close()
        return None


def lookup_tenant_by_token(pairing_token):
    """Look up tenant and connection ID from pairing token."""
    from ..main_db import get_conn as get_main_conn

    token_hash = hash_pairing_token(pairing_token)
    main_conn = get_main_conn()
    try:
        row = main_conn.execute("""
            SELECT tenant_id, connection_id
            FROM local_file_pairing_tokens
            WHERE token_hash = ?
        """, (token_hash,)).fetchone()
        main_conn.close()

        if row:
            return row[0], row[1]
        return None, None
    except:
        main_conn.close()
        return None, None


def disconnect_source(tid, source_id):
    """Revoke/disconnect a data source."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE data_source_connections
            SET status = 'revoked', updated_at = datetime('now')
            WHERE id = ?
        """, (source_id,))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def update_sync_status(tid, source_id, status, error=None):
    """Update sync status and timestamp."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE data_source_connections
            SET last_sync_at = datetime('now'),
                last_sync_status = ?,
                last_sync_error = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (status, error, source_id))
        conn.commit()
        conn.close()
    except:
        conn.close()


def record_change(tid, source_id, debtor_id, change_type, old_values=None, new_values=None):
    """Audit log: record what changed."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("""
            INSERT INTO data_source_changes
            (connection_id, debtor_id, change_type, old_values, new_values)
            VALUES (?, ?, ?, ?, ?)
        """, (
            source_id,
            debtor_id,
            change_type,
            json.dumps(old_values or {}),
            json.dumps(new_values or {})
        ))
        conn.commit()
        conn.close()
    except:
        conn.close()


def detect_conflict(tid, source_id, debtor_id, invoice_id, autostack_values, source_values):
    """Flag a conflict for user resolution."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        existing = conn.execute("""
            SELECT id FROM data_source_conflicts
            WHERE connection_id = ? AND invoice_id = ? AND resolution IS NULL
        """, (source_id, invoice_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE data_source_conflicts
                SET autostack_values = ?, source_values = ?, created_at = datetime('now')
                WHERE id = ?
            """, (
                json.dumps(autostack_values),
                json.dumps(source_values),
                existing[0]
            ))
        else:
            conn.execute("""
                INSERT INTO data_source_conflicts
                (connection_id, debtor_id, invoice_id, autostack_values, source_values)
                VALUES (?, ?, ?, ?, ?)
            """, (
                source_id,
                debtor_id,
                invoice_id,
                json.dumps(autostack_values),
                json.dumps(source_values)
            ))

        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def resolve_conflict(tid, conflict_id, resolution):
    """Resolve a conflict (keep_autostack or use_source)."""
    from ..tenant_db import get_conn

    if resolution not in ("keep_autostack", "use_source"):
        raise ValueError("Invalid resolution")

    conn = get_conn(tid)
    try:
        conn.execute("""
            UPDATE data_source_conflicts
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
    """Get all unresolved conflicts."""
    from ..tenant_db import get_conn

    conn = get_conn(tid)
    try:
        rows = conn.execute("""
            SELECT id, connection_id, debtor_id, invoice_id,
                   autostack_values, source_values
            FROM data_source_conflicts
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
