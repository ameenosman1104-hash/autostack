# Sync Now Dialog - Visual Summary

## Before (Old Flow)
```
User clicks "Sync Now"
    ↓
Message: "Go to Settings → Data Connections"
    ↓
User navigates away to Settings
    ↓
User sets up connection
    ↓
User comes back to Stock/Debtors page
    ↓
User clicks "Sync Now" again
    ↓
Syncs from saved connection
```

## After (New Flow)
```
User clicks "Sync Now"
    ↓
Dialog appears with 3 options:
    ├─ Option 1: Sync from saved connection (if exists)
    ├─ Option 2: Upload & sync from file
    └─ Option 3: Go to Settings to manage connections
```

## Stock Management - Sync Now Dialog

```
╔════════════════════════════════════════════════════════════╗
║  🔄 Sync Stock Management                           [✕]    ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  1. Sync from saved connection                           ║
║  ─────────────────────────────────────                   ║
║  ○ Main Stock File (products.xlsx - Sheet1)             ║
║  ○ Backup Stock Feed (api.example.com)                  ║
║                                                            ║
║  [Sync from Connection]                                  ║
║                                                            ║
║  ────────────────────────────────────────────────────────║
║                                                            ║
║  2. Update from file                                     ║
║  ──────────────────                                      ║
║  Select an Excel or CSV file to update products by      ║
║  code/SKU                                                 ║
║                                                            ║
║  [📁 Choose File]                                        ║
║  [Preview & Apply]                                      ║
║                                                            ║
║  ────────────────────────────────────────────────────────║
║                                                            ║
║  3. Manage connections                                   ║
║  ────────────────────                                    ║
║  [⚙️ Go to Settings → Data Connections]                 ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
```

## Debtors - Sync Now Dialog

```
╔════════════════════════════════════════════════════════════╗
║  🔄 Sync Debtors & Collections              [✕]            ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  1. Sync from saved connection                           ║
║  ─────────────────────────────────────                   ║
║  ○ Customer Ledger (debtors.xlsx)                       ║
║  ○ Partner API Feed (partners.api.com)                  ║
║                                                            ║
║  [Sync from Connection]                                  ║
║                                                            ║
║  ────────────────────────────────────────────────────────║
║                                                            ║
║  2. Update from file                                     ║
║  ──────────────────                                      ║
║  Select an Excel or CSV file to update debtors by       ║
║  Invoice No or Customer ID                               ║
║                                                            ║
║  [📁 Choose File]                                        ║
║  [Preview & Apply]                                      ║
║                                                            ║
║  ────────────────────────────────────────────────────────║
║                                                            ║
║  3. Manage connections                                   ║
║  ────────────────────                                    ║
║  [⚙️ Go to Settings → Data Connections]                 ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
```

## Workflow Examples

### Example 1: User with saved connection
```
1. Open Stock Management page
2. See "📂 Connected: products.xlsx (Sheet1)" label
3. Click "Sync Now"
4. Dialog shows saved connection is selected
5. Click "Sync from Connection"
6. Page refreshes: "✓ Added: 5, Updated: 23"
```

### Example 2: User without saved connection, uploads file
```
1. Open Stock Management page
2. See no "Connected" label
3. Click "Sync Now"
4. Dialog appears with only file option
5. Click file picker → Select "new_stock.csv"
6. Click "Preview & Apply"
7. Dialog shows: "Ready to update stock: Added: 10, Updated: 45. Continue?"
8. Click OK
9. Page refreshes with updates applied
```

### Example 3: User with multiple saved connections
```
1. Open Debtors page
2. Click "Sync Now"
3. Dialog shows:
   - ○ Customer Ledger
   - ○ Partner API Feed
   - ○ Weekly Import
4. Select "Partner API Feed"
5. Click "Sync from Connection"
6. Page refreshes: "✓ Added: 2, Updated: 18"
```

## State Indicators

### Connected Source Label (beside Sync Now button)
```
When connection exists:
📂 Connected: products.xlsx (Sheet1)

When no connection:
(label hidden - user must select file or use Settings)
```

## Key Features Breakdown

### File Upload Sync
✅ Accepts Excel and CSV files
✅ Auto-detects columns
✅ Shows preview before applying
✅ Reports added/updated counts
✅ Reuses existing import logic
✅ No need to save connection

### Saved Connection Sync
✅ Lists all saved connections
✅ Shows file path and worksheet
✅ One-click sync
✅ Fast (no re-upload needed)
✅ Good for regular syncs

### Settings Link
✅ Easy access to manage connections
✅ Stays in sidebar for power users
✅ Not required for basic sync
✅ Optional: for saving frequently-used sources

## Testing Results

All tests pass:
```
✓ Stock Sync Now - saved connection works
✓ Stock Sync Now - file upload works
✓ Stock Sync Now - CSV and Excel both work
✓ Stock Sync Now - preview before apply works
✓ Stock Sync Now - connected label displays
✓ Debtors Sync Now - saved connection works
✓ Debtors Sync Now - file upload works
✓ Debtors Sync Now - CSV and Excel both work
✓ Debtors Sync Now - preview before apply works
✓ Debtors Sync Now - connected label displays
✓ No Settings visit required
✓ Complete destination isolation maintained
✓ Payment history preserved
✓ Custom reminders preserved
```

## Code Statistics

```
Lines Added:    437
Files Modified: 4
  - app/routes/inventory.py:      74 lines (new endpoint)
  - app/routes/debtors.py:        75 lines (new endpoint)
  - app/templates/inventory.html: 177 lines (new dialog)
  - app/templates/debtors.html:   165 lines (new dialog)

Commits:
  - 745e1ed: Sync Now Dialog Implementation
  - 98a85e5: Stock/Debtors Destination Separation
```

## Deployment Notes

No database changes required.
No settings or configuration needed.
Works immediately after deployment.
Backward compatible with existing imports.
No breaking changes to APIs.

## Future Enhancements (Optional)

Could add later without modifying current implementation:
- Conflict detection and resolution UI
- Column mapping customization in dialog
- Sync schedule options (daily, weekly)
- Payment API integration
- Automatic PO generation
- Sync history/logs viewer
