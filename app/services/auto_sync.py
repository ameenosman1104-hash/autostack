"""Automatic live synchronization without manual importing."""
import json, time, threading, os
from datetime import datetime

_SYNC_LOCK = threading.Lock()
_LAST_SYNC = {}

def auto_sync_debtors(tid):
    """Run automatic sync for a tenant's source. Thread-safe, prevents overlapping runs."""
    with _SYNC_LOCK:
        now = time.time()
        last = _LAST_SYNC.get(tid, 0)

        # Prevent overlapping runs (min 5 seconds apart)
        if now - last < 5:
            return {"status": "skipped", "reason": "Sync in progress or too recent"}

        _LAST_SYNC[tid] = now

    try:
        from ..tenant_db import get_all_settings, get_conn, update_sync_status
        from ..services.excel_sync import fetch_source_data, detect_and_apply_changes

        settings = get_all_settings(tid)

        # Check if manual import source is configured
        raw_cfg = settings.get("debtor_import_config", "")
        raw_map = settings.get("debtor_import_mapping", "")

        if not raw_cfg:
            return {"status": "no_source", "message": "No import source configured"}

        try:
            cfg = json.loads(raw_cfg)
            mapping = json.loads(raw_map) if raw_map else {}
        except:
            return {"status": "error", "message": "Corrupted import configuration"}

        # Re-detect email column if not in mapping
        source_type = cfg.get("source", "url")

        # Fetch from source
        headers, rows = fetch_source_data(source_type, cfg)

        if rows is None:
            return {"status": "error", "message": f"Failed to fetch from {source_type}"}

        # Auto-detect email column if missing from mapping
        if "email" not in mapping and headers:
            email_guesses = ["email", "e-mail", "email address", "mail", "contact email"]
            for h in headers:
                if h and h.lower() in email_guesses:
                    mapping["email"] = h
                    # Save updated mapping
                    from ..tenant_db import save_setting
                    save_setting(tid, "debtor_import_mapping", json.dumps(mapping))
                    break

        # Run sync
        unique_key = cfg.get("unique_key", "Invoice No.")
        stats = detect_and_apply_changes(
            tid,
            source_type,
            cfg,
            mapping,
            unique_key,
            external_source="auto_sync"
        )

        # Update sync status
        conn = get_conn(tid)
        try:
            conn.execute("""
                UPDATE debtors
                SET last_synced_at = datetime('now')
                WHERE id IN (
                    SELECT id FROM debtors WHERE external_source = 'auto_sync' LIMIT 1
                )
            """)
            conn.commit()
        except:
            pass
        conn.close()

        return {
            "status": "success",
            "synced_at": datetime.now().isoformat(),
            "added": stats.get("added", 0),
            "updated": stats.get("updated", 0),
            "removed": stats.get("removed", 0),
            "errors": stats.get("errors", [])
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_auto_sync_status(tid):
    """Get the last successful auto-sync timestamp for this tenant."""
    try:
        from ..tenant_db import get_conn

        conn = get_conn(tid)
        row = conn.execute("""
            SELECT MAX(last_synced_at) as last_sync
            FROM debtors
            WHERE external_source = 'auto_sync'
        """).fetchone()
        conn.close()

        if row and row["last_sync"]:
            return {
                "last_synced_at": row["last_sync"],
                "status": "synced"
            }
        return {"last_synced_at": None, "status": "never"}
    except:
        return {"last_synced_at": None, "status": "error"}

# Background sync thread (runs every 15 seconds if enabled)
def start_background_sync(app):
    """Start background sync worker thread."""
    def worker():
        while True:
            try:
                time.sleep(15)

                # Get all tenants with import config
                from ..main_db import get_conn as get_main_conn
                conn = get_main_conn()
                rows = conn.execute("SELECT id FROM tenants WHERE is_active=1").fetchall()
                conn.close()

                for row in rows:
                    tid = row[0]
                    try:
                        result = auto_sync_debtors(tid)
                        if result.get("status") == "success" and (result.get("added") or result.get("updated")):
                            print(f"[AUTO-SYNC] Tenant {tid}: {result}")
                    except Exception as e:
                        print(f"[AUTO-SYNC-ERROR] Tenant {tid}: {e}")

            except Exception as e:
                print(f"[AUTO-SYNC-WORKER-ERROR] {e}")
                time.sleep(5)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print("[AUTO-SYNC] Background worker started")
