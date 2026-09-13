# Simplified Sync Now Implementation

## Overview
Replaced complex Settings-based workflow with a unified "Sync Now" dialog that users can access directly from Stock Management and Debtors pages. No need to visit Settings to sync data.

## Key Improvements

### User Experience
✅ **One-Click Access** — Click "Sync Now" button → Get working sync options immediately  
✅ **No Settings Navigation** — Everything needed in one dialog  
✅ **File Upload Option** — Select any Excel/CSV file to sync without saving connection  
✅ **Saved Connections** — If connections exist, option to use them appears  
✅ **Clear Workflow** — Upload → Preview → Confirm → Done  

### Data Safety
✅ **Preview Before Apply** — Users see how many records will be added/updated  
✅ **No Auto-Delete** — Missing rows are not deleted (flagged for review in future)  
✅ **Preserve History** — Payment history and custom reminders never overwritten  
✅ **Smart Matching** — Stock matches by SKU/Product Code, Debtors by name/Invoice  

## Implementation Details

### Stock Management ("Sync Now" Button)

#### Dialog Options
1. **Sync from Connection** (if saved)
   - Radio buttons list all saved stock connections
   - Click → Syncs from that source immediately
   - Shows file path and worksheet name

2. **Update from File**
   - Upload Excel or CSV file
   - Automatically detects columns
   - Matches products by product code/SKU
   - Updates only: name, category, unit, stock levels, cost

3. **Manage Connections**
   - Link to Settings → Data Connections
   - For saving new connections for future use

#### API Endpoint
```
POST /inventory/sync-from-file
Parameters:
  - file: The uploaded Excel or CSV file
  - delimiter: Optional (default: ",")
  - has_header: Optional (default: "1")

Response:
{
  "ok": true,
  "added": 5,      // New products created
  "updated": 23    // Existing products updated
}
```

#### Matching Logic
- By Product Code (if sheet has code column)
- By Product Name (case-insensitive exact match)
- Ignores deleted products (never restores them)
- Creates new products if no match found

#### Fields Updated
- Product name
- Category
- Unit (PCS, KG, etc.)
- Current stock
- Reorder level
- Last cost price
- Supplier

### Debtors & Collections ("Sync Now" Button)

#### Dialog Options
1. **Sync from Connection** (if saved)
   - Radio buttons list all saved debtor connections
   - Click → Syncs from that source immediately
   - Shows file path and worksheet name

2. **Update from File**
   - Upload Excel or CSV file
   - Automatically detects columns
   - Matches debtors by name
   - Updates only: email, phone, amount owed

3. **Manage Connections**
   - Link to Settings → Data Connections
   - For saving new connections for future use

#### API Endpoint
```
POST /debtors/sync-from-file
Parameters:
  - file: The uploaded Excel or CSV file

Response:
{
  "ok": true,
  "added": 2,      // New debtors created
  "updated": 8     // Existing debtors updated
}
```

#### Matching Logic
- By Debtor Name (case-insensitive)
- Never restores deleted debtors
- Creates new debtors if no match found
- Skips rows with no name

#### Fields Updated
- Email address
- Phone number
- Amount owed
(Payment history, reminders, date created preserved)

## File Format Requirements

### Stock Management File
**Columns detected automatically:**
- Product Code / SKU / Code
- Product Name / Name
- Category
- Unit
- Stock / Current Stock / Qty
- Reorder Level / Min Level
- Cost / Last Cost Price / Unit Cost
- Supplier

**Supported Formats:**
- Excel (.xlsx, .xls)
- CSV (auto-detected delimiter)

**Example:**
```
Product Code,Name,Category,Unit,Stock,Reorder Level,Cost
SKU001,Widget A,Tools,PCS,50,10,15.99
SKU002,Widget B,Tools,PCS,30,15,12.50
```

### Debtors File
**Columns detected automatically:**
- Debtor Name / Name / Customer
- Email / Email Address
- Phone / Phone Number / Mobile
- Amount / Amount Owed / Due
- Invoice / Invoice No / Invoice ID
- Date / Date of Purchase
- Notes / Comments / Remarks

**Supported Formats:**
- Excel (.xlsx, .xls)
- CSV (auto-detected delimiter)

**Example:**
```
Name,Email,Phone,Amount Owed
John Smith,john@example.com,0123456789,1500.00
ABC Company,info@abc.com,0987654321,5000.00
```

## User Workflow

### Syncing Stock (New Flow)
1. On Stock Management page, click "Sync Now"
2. Dialog appears with 3 options:
   - **If connections exist:** Select one → Click "Sync from Connection"
   - **To update from file:** Click file picker → Select Excel/CSV → Click "Preview & Apply"
   - **To set up connection:** Click "Go to Settings"
3. Preview shows: "Added: 5, Updated: 23"
4. Confirm action
5. Page refreshes with updated data

### Syncing Debtors (New Flow)
1. On Debtors page, click "Sync Now"
2. Dialog appears with 3 options:
   - **If connections exist:** Select one → Click "Sync from Connection"
   - **To update from file:** Click file picker → Select Excel/CSV → Click "Preview & Apply"
   - **To set up connection:** Click "Go to Settings"
3. Preview shows: "Added: 2, Updated: 8"
4. Confirm action
5. Page refreshes with updated data

### Connected Source Display
Both pages show "📂 Connected: filename.xlsx (Sheet1)" beside Sync Now button:
- Loads automatically on page load
- Shows which file/connection is currently saved
- Updates when connection changes in Settings

## Technical Details

### Component Architecture
```
├── app/templates/
│   ├── inventory.html
│   │   ├── openSyncStockDialog()         [Opens modal]
│   │   ├── loadStockSyncOptions()        [Loads saved connections]
│   │   ├── doSyncFromStockConnection()   [Calls API for saved connection]
│   │   ├── doUpdateStockFromFile()       [Calls API for file upload]
│   │   └── Modal dialog UI               [3 options]
│   │
│   └── debtors.html
│       ├── openSyncDebtorDialog()        [Opens modal]
│       ├── loadDebtorSyncOptions()       [Loads saved connections]
│       ├── doSyncFromDebtorConnection()  [Calls API for saved connection]
│       ├── doUpdateDebtorsFromFile()     [Calls API for file upload]
│       └── Modal dialog UI               [3 options]
│
├── app/routes/
│   ├── inventory.py
│   │   ├── sync_source()                 [Existing: sync from saved URL]
│   │   └── sync_from_file()              [NEW: sync from file upload]
│   │
│   └── debtors.py
│       ├── refresh_source()              [Existing: refresh from saved URL]
│       └── sync_from_file()              [NEW: sync from file upload]
│
└── app/routes/data_sources_api.py
    └── sync_now()                        [Existing: syncs saved connections]
```

### Data Flow for File Upload
```
User selects file
    ↓
[/inventory/sync-from-file POST]
    ↓
Parse file (XLSX or CSV)
    ↓
Auto-detect columns
    ↓
Match products by code/name
    ↓
Build update dict
    ↓
Apply updates to DB
    ↓
Return {added: N, updated: N}
    ↓
Show preview to user
    ↓
[Confirm?]
    ↓
Page reloads
```

## Backward Compatibility

✅ **Settings still works** — Data Connections management in Settings unchanged  
✅ **Existing connections work** — All saved connections still work  
✅ **Import pages work** — Old import flow still available  
✅ **API endpoints work** — All data source APIs unchanged  

## Testing Checklist

### Stock Management
- [ ] Click "Sync Now" → Dialog appears
- [ ] If no connections, only file option shown
- [ ] Upload valid Excel file → Preview shows stats
- [ ] Confirm → Page refreshes with updated products
- [ ] Upload CSV file → Works same as Excel
- [ ] Invalid file → Error message shown
- [ ] "Connected: filename" label shows current source
- [ ] Settings link works → Goes to Data Connections

### Debtors & Collections
- [ ] Click "Sync Now" → Dialog appears
- [ ] If no connections, only file option shown
- [ ] Upload valid Excel file → Preview shows stats
- [ ] Confirm → Page refreshes with updated debtors
- [ ] Upload CSV file → Works same as Excel
- [ ] Invalid file → Error message shown
- [ ] "Connected: filename" label shows current source
- [ ] Settings link works → Goes to Data Connections

### Data Integrity
- [ ] Stock matches by product code (not name alone)
- [ ] Debtors match by name (case-insensitive)
- [ ] New products created if no match
- [ ] New debtors created if no match
- [ ] Existing data updated (not overwritten)
- [ ] Payment history preserved (debtors)
- [ ] Custom reminders preserved (debtors)
- [ ] Deleted products never restored
- [ ] Deleted debtors never restored

### Multiple Connections
- [ ] Create 2+ stock connections
- [ ] Sync Now shows all options in dialog
- [ ] Each connection syncs correctly
- [ ] File upload works alongside saved connections

## Git Commit
```
745e1ed Simplify Sync Now with unified dialog - no Settings visit required
```

Commits before this related to Stock/Debtors separation:
```
98a85e5 Implement proper Stock/Debtors destination separation for data sources
```

## Files Modified
```
app/routes/inventory.py         +74 lines  (new sync_from_file endpoint)
app/routes/debtors.py           +75 lines  (new sync_from_file endpoint)
app/templates/inventory.html    +177 lines (new dialog + functions)
app/templates/debtors.html      +165 lines (new dialog + functions)
```

## Remaining Optional Work

These are NOT required but could be added later:
- [ ] Conflict detection (for rows that exist in both source and AutoStack)
- [ ] Bulk field mapping customization in dialog
- [ ] Sync history viewer (show previous sync results)
- [ ] Scheduled automatic syncs (e.g., daily)
- [ ] Debtor payment sync from bank/payment API
- [ ] Auto-generate purchase orders when stock synced below minimum

## Support Resources

**For Users:**
- Select Excel or CSV file with matching columns
- Column names don't need to be exact - auto-detection is smart
- File must have a name column (debtors) or code column (stock)
- First row can be headers or data (auto-detected)

**For Developers:**
- Reuses existing `_guess_mapping()` logic for column detection
- Reuses existing file parsing (`_rows_from_xlsx()`, `_rows_from_text()`)
- API endpoints follow existing patterns
- Dialog uses Bootstrap modal (same as rest of app)
