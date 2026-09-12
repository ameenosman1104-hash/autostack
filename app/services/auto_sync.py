"""Automatic live synchronization without manual importing."""
import json, time, threading, os
from datetime import datetime

_SYNC_LOCK = threading.Lock()
_LAST_SYNC = {}

def sync_data_source(tid, source_id):
    """Sync a single data source (hosted URL or API). Not for local files."""
    from ..tenant_db import get_conn
    from ..services.data_sources import get_data_source, update_sync_status
    from ..services.excel_sync import fetch_source_data, detect_and_apply_changes

    try:
        source = get_data_source(tid, source_id)
        if not source or source["source_type"] == "local_file":
            return {"status": "skipped", "reason": "Local files are connector-managed"}

        source_type = source["source_type"]
        config = source["config"]
        mapping = source["column_mapping"]
        unique_key = source["unique_key_field"]

        # Fetch from source
        headers, rows = fetch_source_data(source_type, config)
        if rows is None:
            msg = f"Failed to fetch from {source_type}"
            update_sync_status(tid, source_id, "error", msg)
            return {"status": "error", "message": msg}

        if not rows:
            update_sync_status(tid, source_id, "success")
            return {"status": "success", "synced_rows": 0, "added": 0, "updated": 0, "removed": 0}

        # Transform field names to column names
        snapshot_transformed = []
        for row in rows:
            transformed_row = {}
            for field, value in row.items():
                col_name = mapping.get(field, field)
                transformed_row[col_name] = value
            snapshot_transformed.append(transformed_row)

        # Detect and apply changes
        stats = detect_and_apply_changes(
            tid, source_type, config, mapping, unique_key,
            snapshot_data=snapshot_transformed,
            external_source=f"{source_type}:{source_id}"
        )

        update_sync_status(tid, source_id, "success")
        return {
            "status": "success",
            "synced_rows": len(snapshot_transformed),
            "added": stats.get("added", 0),
            "updated": stats.get("updated", 0),
            "removed": stats.get("removed", 0),
            "errors": stats.get("errors", [])
        }

    except Exception as e:
        update_sync_status(tid, source_id, "error", str(e))
        return {"status": "error", "message": str(e)}


def auto_sync_debtors(tid):
    """Run automatic sync for all data sources (hosted URL and API). Local files handled by connector."""
    with _SYNC_LOCK:
        now = time.time()
        last = _LAST_SYNC.get(tid, 0)

        # Prevent overlapping runs (min 5 seconds apart)
        if now - last < 5:
            return {"status": "skipped", "reason": "Sync in progress or too recent"}

        _LAST_SYNC[tid] = now

    try:
        from ..tenant_db import get_conn
        from ..services.data_sources import list_data_sources

        # Get all active data sources
        sources = list_data_sources(tid)
        if not sources:
            return {"status": "no_sources", "message": "No data sources configured"}

        results = []
        for source in sources:
            if source["source_type"] != "local_file":
                result = sync_data_source(tid, source["id"])
                results.append({
                    "source_id": source["id"],
                    "source_name": source["name"],
                    **result
                })

        return {
            "status": "success",
            "sources_synced": len([r for r in results if r.get("status") == "success"]),
            "sources_failed": len([r for r in results if r.get("status") == "error"]),
            "results": results
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
