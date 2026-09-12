# Live Excel Sync Setup Guide

## Overview

AutoStack can synchronize debtor data from Excel workbooks stored in OneDrive or SharePoint via Microsoft Graph API. Changes—including email-only updates—automatically sync to AutoStack without manual imports or page refreshes.

**Status:** Foundation implemented. Microsoft Graph API integration pending (requires live testing with real Excel files in OneDrive).

## Architecture

- **Excel OAuth Module** (`app/excel_oauth.py`): Handles Microsoft account authentication
- **Live Sync Worker** (`app/services/live_excel_sync.py`): Checks for changes and applies syncs
- **Database Migration**: `live_excel_connections` table stores connection metadata
- **Sync Logic**: Reuses existing `detect_and_apply_changes()` framework

## How It Works

1. **Connect Excel**: User authorizes their Microsoft account in Settings > Email/Sync
2. **Select Workbook**: Choose OneDrive/SharePoint file and worksheet
3. **Map Columns**: Select columns for Name, Email, Amount, etc.
4. **Auto-Sync**: Every ~15 seconds (configurable), worker checks for changes
5. **Update Debtors**: Matches by Invoice No. or custom ID; updates existing, creates new
6. **Preserve Settings**: Custom reminders, manual edits stay intact

## Required Microsoft App Registration

### 1. Create Azure App

- Go to [Azure Portal](https://portal.azure.com)
- **App Registrations** → **New registration**
- Name: `AutoStack Excel Sync`
- Supported account types: Accounts in any organizational directory (Multi-tenant)
- Redirect URI: `https://your-render-url.onrender.com/settings/excel/callback`
  (Also add `http://localhost:5000/settings/excel/callback` for local dev)

### 2. Configure API Permissions

- Select your app
- **API Permissions** → **Add a permission**
- **Microsoft Graph**
  - Delegated permissions:
    - `Files.Read` — Read Excel files
    - `offline_access` — Keep user signed in
  - Application permissions: (Not needed for this flow)

- Click **Grant admin consent** (if you have admin access)

### 3. Create Client Secret

- **Certificates & secrets** → **New client secret**
- Description: `Excel Sync`
- Expiry: 24 months or longer
- **Copy the secret value** (you'll only see it once)

### 4. Get Application Credentials

From Overview page:
- **Application (client) ID** — Copy this
- **Client secret** — You copied this above

## Environment Variables

Add to Render (or local `.env` for development):

```
EXCEL_CLIENT_ID=your_application_client_id
EXCEL_CLIENT_SECRET=your_client_secret_value
EXCEL_REDIRECT_URI=https://your-render-url.onrender.com/settings/excel/callback
```

## Render Background Worker (Planned)

The live sync worker requires a background task runner. Recommended setup:

```bash
# In Render: Add a new service
Service: Web Service (or Background Worker if available)
Name: Excel Sync Worker
Build Command: (same as main app)
Start Command: python -c "from app import create_app; from app.services.live_excel_sync import check_and_sync_excel; app = create_app(); ..."
```

Or use APScheduler within the main app (simpler, requires main app always running).

## Database Schema

```sql
CREATE TABLE live_excel_connections (
    id INTEGER PRIMARY KEY,
    connection_name TEXT,              -- User-friendly name
    workbook_id TEXT,                  -- OneDrive/SharePoint file ID
    worksheet_name TEXT,               -- Sheet name
    table_name TEXT,                   -- Excel table name (optional)
    access_token TEXT,                 -- Encrypted Bearer token
    refresh_token TEXT,                -- Encrypted refresh token
    expires_at INTEGER,                -- Token expiry (Unix timestamp)
    graph_user_id TEXT,                -- Microsoft user ID
    column_mapping TEXT,               -- JSON: {"name": "Name", "email": "Email", ...}
    unique_key_field TEXT,             -- Column for dedup (e.g., "Invoice No.")
    last_sync_at TEXT,                 -- ISO timestamp of last sync
    last_sync_status TEXT,             -- "success", "error", "pending"
    last_sync_error TEXT,              -- Error message if failed
    sync_interval_seconds INTEGER,     -- Check interval (~15 recommended)
    created_at TEXT,
    updated_at TEXT
);
```

## Detected Changes

The sync recognizes:

- **New rows** → Create new debtor
- **Updated cells** → Refresh matching debtor (Email, Name, Amount, etc.)
- **Removed rows** → Mark as `sync_status="removed_from_source"` (flagged for review, not auto-deleted)
- **Email-only change** → Updates existing debtor, preserves all other fields

**Dedup Logic**: Matches by `external_key` (Invoice No. or configured ID column). Same row edited in Excel and AutoStack → Flagged as conflict; user resolves.

## Data Preservation

- ✅ Custom reminder settings kept intact
- ✅ Manual email entries not cleared if spreadsheet cell is blank
- ✅ Payment history preserved
- ✅ Audit trail of all sync changes logged

## Testing Checklist

- [ ] User connects Excel in Settings
- [ ] Change only an email cell, sync → Email updates
- [ ] Change amount, sync → Debtor amount updates
- [ ] Add new row, sync → New debtor created
- [ ] Repeat sync → No duplicates
- [ ] Remove row, sync → Flagged as removed (not deleted)
- [ ] Custom reminder survives sync
- [ ] Edit same debtor in Excel and AutoStack → Conflict flagged
- [ ] Refresh page → Excel connection persists
- [ ] Close browser, wait 30s → Sync still runs in background
- [ ] Disconnect Excel → Manual import still available

## Limitations & Notes

- **File must stay in OneDrive/SharePoint**: Local file imports are one-time snapshots only
- **Token refresh**: Automatic when expired; user sees "Reconnect Excel" if revoked
- **Rate limiting**: Microsoft Graph has throttling; sync respects 429 responses
- **Column matching**: Case-sensitive by default; mappings stored in `column_mapping` JSON
- **Conflicts**: User must resolve if same record edited in both places within one sync cycle

## Troubleshooting

**"Excel not connected" in Status**
→ Check Settings > Excel connection exists and refresh token hasn't expired

**"Failed to fetch from source"**
→ Check Microsoft Graph API permissions, network, and workbook file still exists

**"Duplicate debtors created"**
→ Verify `unique_key_field` is correct and unique for each invoice
→ Check sync logs for conflicts

**Changes not syncing**
→ Verify background worker is running (`ps aux | grep excel`)
→ Check last_sync_at timestamp is recent
→ Review last_sync_error in database

## Current Implementation Status

✅ Database migration (table + schema)
✅ Microsoft Graph OAuth flow
✅ Token encryption & storage
✅ Tenant isolation
✅ Unit tests (3 tests, all passing)
✅ Connection UI placeholder
✅ Existing sync logic reuse

⏳ Microsoft Graph API fetch (reads workbook data)
⏳ Background worker scheduling
⏳ Conflict detection & user UI
⏳ Email-only change detection
⏳ Live preview in Settings
⏳ Full integration testing with real Excel

## Next Steps

1. Register Microsoft app (above)
2. Set environment variables on Render
3. Deploy code (`git push`)
4. Configure background worker on Render
5. Test with Excel file in OneDrive
6. Implement Graph API data fetch (currently placeholder)
7. Run live sync tests with real workbook changes

---

**Note**: Live sync is architectural complete but requires Microsoft Graph API data-fetch implementation and Render worker setup for production use.
