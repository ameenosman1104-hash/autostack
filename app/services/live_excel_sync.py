"""Live Excel sync worker for OneDrive/SharePoint via Microsoft Graph."""
import json, time
from ..excel_oauth import get_excel_connection
from .excel_sync import detect_and_apply_changes

def sync_excel_live(tid):
    """Execute one live sync cycle for a tenant's Excel connection."""
    try:
        conn_data = get_excel_connection(tid)
        if not conn_data:
            return {"status": "no_connection", "error": "Excel not connected"}

        # TODO: Refresh token if needed
        # TODO: Fetch from Microsoft Graph API
        # For now, this is a placeholder for the actual Graph API integration

        access_token = conn_data.get("access_token")
        workbook_id = conn_data.get("workbook_id")
        worksheet_name = conn_data.get("worksheet_name")
        table_name = conn_data.get("table_name")

        # Fetch from Microsoft Graph (placeholder - actual implementation requires requests lib)
        # headers = {"Authorization": f"Bearer {access_token}"}
        # url = f"https://graph.microsoft.com/v1.0/me/drive/items/{workbook_id}/workbook/worksheets/{worksheet_name}/tables/{table_name}/rows"

        # For now, return placeholder
        return {
            "status": "pending",
            "message": "Excel sync worker initialized. Awaiting Microsoft Graph implementation."
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}

def check_and_sync_excel(tid):
    """Check if sync is due and execute if needed."""
    try:
        from ..tenant_db import get_conn

        conn = get_conn(tid)
        row = conn.execute(
            "SELECT last_sync_at, sync_interval_seconds FROM live_excel_connections LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            return False

        last_sync = row[0]
        interval = row[1] or 15

        if not last_sync:
            return True

        import datetime
        last = datetime.datetime.fromisoformat(last_sync)
        now = datetime.datetime.now()
        return (now - last).total_seconds() >= interval

    except:
        return False

def update_sync_status(tid, status, error=None):
    """Update sync status in database."""
    try:
        from ..tenant_db import get_conn
        import datetime

        conn = get_conn(tid)
        conn.execute(
            """UPDATE live_excel_connections
               SET last_sync_at=?, last_sync_status=?, last_sync_error=?
               WHERE id=1""",
            (datetime.datetime.now().isoformat(), status, error)
        )
        conn.commit()
        conn.close()
    except:
        pass
