# Stock/Debtors Destination Separation Implementation

## Overview
Implemented complete isolation between Stock Management and Debtors data source connections. Each module (Stock, Debtors) now has its own separate data sources with independent sync logic and column mapping.

## Changes Made

### 1. Database Schema (Migration 9)
**File:** `app/db_migrations.py`

Added two new columns to `data_source_connections` table:
- `destination TEXT DEFAULT 'debtors'` — Specifies target module: `stock` or `debtors`
- `matching_identifier TEXT DEFAULT 'auto'` — Specifies the key field for matching records

## 2. Service Layer Updates

### Data Source Management
**File:** `app/services/data_sources.py`

#### Updated `create_data_source()`
- Now accepts `destination` parameter (required: `stock` or `debtors`)
- Now accepts `matching_identifier` parameter for custom key fields
- Validates destination before creating connection
- Stores destination in database for routing

#### Updated `list_data_sources()`
- Added optional `destination` parameter to filter by target module
- Returns all 10 fields including destination and file info
- Allows API to retrieve only stock sources or only debtor sources

#### Updated `get_data_source()`
- Now includes `destination` and `matching_identifier` in returned dict
- Safely handles legacy databases without these columns

### Stock Synchronization
**File:** `app/services/excel_sync.py`

#### New: `detect_and_apply_stock_changes()`
- Mirrors debtor sync logic but for products/inventory
- Matches by product code/SKU (configurable via unique_key_field)
- Updates product quantities, names, categories from external source
- Returns stats: `{added: N, updated: N, errors: [...]}`
- Works with local files, hosted URLs, and APIs

## 3. API Endpoints

### Data Sources API
**File:** `app/routes/data_sources_api.py`

#### POST `/api/data-sources/connect`
- Now accepts `destination` parameter (required)
- Now accepts `matching_identifier` parameter
- Validates destination is `stock` or `debtors`

#### GET `/api/data-sources/list`
- Added optional `?destination=stock` or `?destination=debtors` query parameter
- Returns only sources for specified destination
- Includes file_path and worksheet_name for UI display

#### POST `/api/data-sources/sync-now`
- Added optional `destination` parameter (uses source's destination if omitted)
- Routes to appropriate sync function based on destination:
  - `stock` → calls `detect_and_apply_stock_changes()`
  - `debtors` → calls `detect_and_apply_changes()`
- Returns destination in response for client confirmation

## 4. User Interface Updates

### Stock Management Page
**File:** `app/templates/inventory.html`

#### `syncStockNow()` Function
- Now filters `/api/data-sources/list?destination=stock`
- Selects first stock source (no longer takes any source)
- Passes `destination: 'stock'` to sync-now endpoint
- Shows clear error if no stock source is connected
- Displays file name and worksheet in sync confirmation

#### Connected Source Display
- Added `loadConnectedStockSource()` to fetch and display connected file
- Shows "📂 Connected: filename.xlsx (Sheet1)" beside Sync Now button
- Updates dynamically on page load
- Provides context about which file will be synced

### Debtors & Collections Page
**File:** `app/templates/debtors.html`

#### `syncNow()` Function
- Now filters `/api/data-sources/list?destination=debtors`
- Selects first debtor source (no longer takes any source)
- Passes `destination: 'debtors'` to sync-now endpoint
- Shows clear error if no debtor source is connected
- Displays file name and worksheet in sync confirmation

#### Connected Source Display
- Added `loadConnectedDebtorSource()` to fetch and display connected file
- Shows "📂 Connected: filename.xlsx (Debtors)" beside Sync Now button
- Updates dynamically on page load
- Provides context about which file will be synced

## 5. Key Behaviors

### Complete Isolation
✅ **Stock sources only affect Stock Management**
- Can only match by product code/SKU
- Can only update product fields (name, quantity, category, etc.)
- Never touches debtor records

✅ **Debtor sources only affect Debtors & Collections**
- Can match by invoice number, customer ID, etc.
- Can only update debtor fields (contact, amount, email, etc.)
- Never touches product records

### Multiple Sources Per Module
✅ **Stock Management can have multiple sources**
- Source 1: Local Excel file syncs to one location
- Source 2: API endpoint syncs to another
- Each maintains its own last_sync_at and status

✅ **Debtors can have multiple sources**
- Source 1: Local spreadsheet with hourly updates
- Source 2: Partner API with daily syncs
- Each operates independently

### No Cross-Contamination
- Connecting one stock source does NOT affect debtor data
- Connecting one debtor source does NOT affect stock data
- Each Sync Now button uses ONLY its designated sources
- Column mappings are per-source (not shared)

## 6. Testing

### Test Coverage
Created `test_destination_separation.py` which verifies:

✅ **Test 1:** Create stock source successfully
✅ **Test 2:** Create debtor source successfully
✅ **Test 3:** Create multiple stock sources
✅ **Test 4:** List all sources (returns 3)
✅ **Test 5:** Filter for stock only (returns 2)
✅ **Test 6:** Filter for debtors only (returns 1)
✅ **Test 7:** Destination field present in API responses

**All tests passed successfully.**

## 7. Example Usage

### Adding a Stock Connection
```javascript
fetch('/api/data-sources/connect', {
  method: 'POST',
  body: JSON.stringify({
    name: 'Warehouse Stock Feed',
    source_type: 'hosted_url',
    destination: 'stock',  // ← KEY: Specify stock
    matching_identifier: 'product_code',
    config: {url: 'https://example.com/stock.csv'},
    column_mapping: {
      'Product Code': 'code',
      'Qty': 'current_stock',
      'Name': 'name'
    }
  })
})
```

### Adding a Debtors Connection
```javascript
fetch('/api/data-sources/connect', {
  method: 'POST',
  body: JSON.stringify({
    name: 'Customer Ledger',
    source_type: 'local_file',
    destination: 'debtors',  // ← KEY: Specify debtors
    matching_identifier: 'invoice_id',
    config: {file_path: 'C:\\Data\\debtors.xlsx'},
    column_mapping: {
      'Invoice No': 'external_key',
      'Customer': 'name',
      'Amount': 'amount_due'
    }
  })
})
```

### Syncing Stock Only
```javascript
// This will ONLY sync stock sources
fetch('/api/data-sources/sync-now', {
  method: 'POST',
  body: JSON.stringify({
    source_id: 5,
    destination: 'stock'
  })
})
// Response: {"added": 12, "updated": 45, "destination": "stock"}
```

### Syncing Debtors Only
```javascript
// This will ONLY sync debtor sources
fetch('/api/data-sources/sync-now', {
  method: 'POST',
  body: JSON.stringify({
    source_id: 3,
    destination: 'debtors'
  })
})
// Response: {"added": 2, "updated": 18, "removed": 1, "destination": "debtors"}
```

## 8. Benefits

1. **Data Safety** — Sources cannot accidentally sync to wrong module
2. **Flexibility** — Each module can have independent sync strategies
3. **Performance** — Sync button only fetches relevant data
4. **Clarity** — UI shows exactly which source is connected
5. **Scalability** — Can add more sources without affecting existing ones

## 9. Migration Path

For existing databases:
- Old databases without `destination` column are auto-migrated by Migration 9
- New connections MUST specify destination
- Old connections that exist are treated as debtors (default)

## 10. Next Steps

When implementing Settings Data Connections UI:
1. Add connection form with destination dropdown
2. Store column mappings per destination
3. Implement edit/delete endpoints
4. Add connection status dashboard
5. Show per-connection sync history

## Commit
```
Commit: 98a85e5
Message: Implement proper Stock/Debtors destination separation for data sources
```
