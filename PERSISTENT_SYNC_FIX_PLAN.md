# Persistent File Synchronization - Complete Fix Plan

## ROOT CAUSES CONFIRMED

###  1. IndexedDB Cannot Persist FileSystemFileHandle Objects
- FileSystemFileHandle objects are NOT serializable to IndexedDB
- When page reloads, the handle is lost or corrupted
- Every user action creates a NEW IndexedDB entry instead of reusing existing connections
- This causes the "three entries with the same filename" issue

### 2. No Database Integration for File Uploads
- `sync_from_file()` endpoints process files but DON'T create `data_source_connections` entries
- File uploads are ephemeral - not saved anywhere the next page load can find them
- IndexedDB is browser-side only; it's not the database

### 3. Duplicate Connections Created
- Each file selection creates a new IndexedDB entry
- No mechanism to check if a file has been saved before
- No way to update an existing connection (always creates new)
- File path + worksheet name is not used as a connection ID

### 4. Browser Security Limitation
- HTML5 `<input type="file">` File objects cannot be persisted
- After page reload, you have NO WAY to access that file again (browser security)
- File System Access API's FileSystemFileHandle CAN persist... but only in memory for the session

---

## COMPLETE SOLUTION

### PHASE 1: Database-Backed Connections (CRITICAL)

**Problem**: File uploads don't create database entries. Fix this first.

**Action**: When user selects a file via "Select File" button:

```javascript
// When user confirms file preview:
async function createFileConnection(filename, fileObject, worksheetName) {
  const response = await fetch('/api/data-sources/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: filename.split('.')[0],  // "Debtors.xlsx" → "Debtors"
      source_type: 'local_file',
      destination: 'debtors',  // or 'stock'
      config: {
        file_path: filename,  // Just for display; browsers can't reuse this
        worksheet_name: worksheetName  // Remember which sheet user selected
      },
      column_mapping: {},
      unique_key_field: 'Invoice No.'
    })
  });
  
  const data = await response.json();
  return data.source_id;  // Get the connection ID from database
}
```

This creates a persistent database entry. Now the connection survives page reloads.

**Database Changes**: None needed. Use existing `data_source_connections` table.

---

### PHASE 2: Two Real Persistent Access Paths

The critical insight: **There are only TWO ways to persist file access across browser sessions:**

#### **Option A: Browser Session Only** (What we have now, needs honesty)
- User selects file via showOpenFilePicker()
- FileSystemFileHandle stored in **memory** (sessionStorage or a JS variable)
- Works until page reload
- On reload: **Cannot restore the handle** (browser limitation, not a bug)
- **Show in UI**: "Using File System Access API (current session only)"
- **When page reloads**: Show button to "Select file again" → calls showOpenFilePicker()

#### **Option B: Windows Background Connector** (Real persistence)
- User runs Windows app: `python local_file_connector.py --setup`
- Selects file path once
- Windows app monitors that file 24/7
- Syncs changes automatically every 15 seconds
- Survives page reloads, browser restarts, computer restarts (if computer stays on)
- **Show in UI**: "Connected via Background Connector (monitoring file...)"
- **Status**: Green checkmark if connector is online, warning if offline

---

### PHASE 3: JavaScript Rewrite (Correct Approach)

**Remove all broken IndexedDB persistence code.** Replace with:

```javascript
// Session-based file handle storage (cleared on reload)
let sessionFileHandles = {};  // Map: connection_id → FileSystemFileHandle

// Create file connection in database
async function createFileConnection(file, worksheetName) {
  // 1. Create database connection
  const connResp = await fetch('/api/data-sources/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: file.name.split('.')[0],
      source_type: 'local_file',
      destination: currentModule,  // 'stock' or 'debtors'
      config: {
        file_path: file.name,  // Display only
        worksheet_name: worksheetName
      },
      column_mapping: {},
      unique_key_field: 'Invoice No.'
    })
  });
  
  const connData = await connResp.json();
  const connectionId = connData.source_id;
  
  // 2. Store handle in SESSION memory (not IndexedDB!)
  // Only available for this page session
  try {
    if (typeof file.handle !== 'undefined') {
      sessionFileHandles[connectionId] = file.handle;
    }
  } catch (e) {
    // File doesn't have a handle (regular file input)
  }
  
  return connectionId;
}

// Sync from saved connection
async function syncFromConnection(connectionId) {
  // Check if we have a session handle
  const handle = sessionFileHandles[connectionId];
  
  if (handle) {
    // We have the handle from showOpenFilePicker
    // Use it for this sync
    const file = await handle.getFile();
    return syncFile(file, connectionId);
  } else {
    // Handle is gone (page reloaded or showOpenFilePicker wasn't used)
    // Offer user two options:
    showSyncOptions(connectionId);
  }
}

function showSyncOptions(connectionId) {
  const msg = `This file connection was created in a previous browser session.
  
Options:
1. Click "Select file again" to use File System Access API (current session)
2. Set up the Windows Connector for automatic background sync
3. Remove this connection and create a new one`;
  
  if (confirm(msg)) {
    openFileSelectionDialog();
  }
}
```

**Key principle**: 
- Database stores everything that survives reloads (metadata, column mapping, etc.)
- Session memory stores only the FileSystemFileHandle (can't survive reload)
- On page reload: Show "Select file again" or "Use Connector" buttons

---

### PHASE 4: Windows Connector Setup (Real Persistence)

Existing code is in `connector/local_file_connector.py`. It works. We need UI for it.

**In Sync Now modal**, add section:

```html
<div class="alert alert-info">
  <h6>For Automatic Background Sync:</h6>
  <p>The Windows Background Connector monitors your file 24/7 and syncs automatically.</p>
  
  <ol>
    <li>Download and install: <code>python local_file_connector.py --setup</code></li>
    <li>Select your Excel file
    <li>Generate a pairing token in AutoStack Settings
    <li>Complete the setup
  </ol>
  
  <button onclick="openConnectorSetupGuide()">View Setup Instructions</button>
  <p class="small text-muted">Your computer must be on for automatic syncing.</p>
</div>
```

---

### PHASE 5: Stop Creating Duplicates

**Current problem**: Every time user selects a file, a NEW connection is created.

**Fix**: Check if connection already exists before creating.

```javascript
async function selectFile() {
  const [fileHandle] = await showOpenFilePicker();
  const file = await fileHandle.getFile();
  const worksheetName = '';  // Ask user if XLSX
  
  // CHECK: Does a connection exist for this file + worksheet?
  const existing = await fetch(
    `/api/connections?module=${currentModule}&file_name=${file.name}`
  ).then(r => r.json());
  
  if (existing.connections && existing.connections.length > 0) {
    // Reuse existing connection
    connectionId = existing.connections[0].id;
  } else {
    // Create NEW connection
    connectionId = await createFileConnection(file, worksheetName);
  }
  
  // Store handle in session memory
  sessionFileHandles[connectionId] = fileHandle;
  
  // Sync immediately
  return syncFromConnection(connectionId);
}
```

**Database change needed**: Add unique index on (tenant_id, source_type, file_path, destination) to prevent duplicates.

---

### PHASE 6: Honest UI Messaging

Replace misleading "Persistent" button with honest options:

```
┌────────────────────────────────────────────────┐
│ Saved Connections                              │
├────────────────────────────────────────────────┤
│ ✓ Debtors.xlsx (Current session)               │
│   Last sync: 2 minutes ago                     │
│   [Sync Now] [Select Different File] [Remove] │
│                                                │
│ ✓ API: ERP System  (Always available)          │
│   Last sync: 5 minutes ago                     │
│   [Sync Now] [Change] [Remove]                │
│                                                │
│ ⚠ Invoices.xlsx (Needs Connector)              │
│   Status: Not connected. Set up the Windows   │
│   Background Connector for automatic sync.    │
│   [Setup Instructions] [Remove]                │
└────────────────────────────────────────────────┘
```

**Connection Types**:
- `(Current session)` - Using showOpenFilePicker, survives until reload
- `(Always available)` - API or hosted URL, survives everything
- `(Needs Connector)` - Local file that could use background monitoring

---

## ACCEPTANCE TEST (End-to-End)

```
✓ TEST 1: Create connection, reload page, sync again
  1. Sync Now → Select File → Choose debtors.xlsx
  2. File preview, confirm mapping
  3. Click Sync → data syncs, page reloads
  4. Refresh browser (Ctrl+F5, hard reload)
  5. Sync Now → Connection still listed
  6. Click Sync → Browser asks for permission again (expected)
  7. Grant permission
  8. Data syncs without selecting file again
  9. ✓ PASS: One connection, reused across reload

✓ TEST 2: No duplicates from repeated file selection
  1. Sync Now → Select products.xlsx
  2. Sync → creates connection ID 5
  3. Back to Sync Now → Select same file again
  4. Check database: still connection ID 5 (not ID 6)
  5. Sync → data syncs
  6. Repeat 3-5 five times
  7. Database shows only ONE connection ID 5
  8. ✓ PASS: No duplicate connections created

✓ TEST 3: Different files with same name don't conflict
  1. Sync two files both named "data.xlsx" from different folders
  2. Create connection A: C:\Users\John\data.xlsx
  3. Create connection B: C:\Users\Mary\data.xlsx
  4. Check database: Two separate connections (different folder paths)
  5. Sync A → syncs from C:\Users\John
  6. Sync B → syncs from C:\Users\Mary
  7. ✓ PASS: Different files treated separately

✓ TEST 4: Worksheet selection saved
  1. Sync Excel file with multiple sheets
  2. Choose "Sheet2" 
  3. Save connection
  4. Reload page
  5. Sync again → Uses "Sheet2" automatically
  6. ✓ PASS: Worksheet preference saved

✓ TEST 5: API connections still work
  1. Add API connection to ERP endpoint
  2. Reload page
  3. Sync Now → Connection listed, works
  4. ✓ PASS: APIs unaffected

✓ TEST 6: File moved/deleted shows honest error
  1. Create connection to C:\Users\test.xlsx
  2. Delete test.xlsx from disk
  3. Try to use connector
  4. Error: "File not found. It may have been moved or deleted."
  5. Options: [Select Different File] [Use Connector] [Remove Connection]
  6. ✓ PASS: Clear error message, recovery options shown
```

---

## IMPLEMENTATION CHECKLIST

- [ ] **Backend**: sync_from_file() accepts connection_id (DONE)
- [ ] **Backend**: Create API endpoint `POST /api/data-sources/connect` to create file connections
- [ ] **Backend**: Add unique constraint on (tenant_id, source_type, file_path, destination)
- [ ] **Frontend**: Remove all IndexedDB persistence code
- [ ] **Frontend**: Keep FileSystemFileHandle in sessionStorage only (current session)
- [ ] **Frontend**: Show honest UI labels ("current session", "always available", "needs connector")
- [ ] **Frontend**: On page reload, check if handle is still available
- [ ] **Frontend**: If handle lost, show "Select file again" button
- [ ] **Frontend**: Add Windows Connector setup instructions in modal
- [ ] **Frontend**: Prevent duplicate connection creation (check before create)
- [ ] **Frontend**: Remove "Persistent" button (was misleading)
- [ ] **Tests**: Run all 6 acceptance tests above
- [ ] **Docs**: Document browser limitations and connector setup

---

## Timeline

- **Quick Fix (1-2 hours)**: Backend changes + stop IndexedDB creation = fewer duplicates
- **Proper Fix (4-6 hours)**: Complete rewrite above = real persistent access
- **Connector UI (2-3 hours)**: Windows connector setup instructions

---

## Key Files to Change

1. `app/routes/data_sources_api.py` - Create new endpoint for file connections
2. `app/templates/inventory.html` - Remove IndexedDB code, add session storage
3. `app/templates/debtors.html` - Same as inventory
4. `app/db_migrations.py` - Add unique constraint (Migration 11)
5. `connector/local_file_connector.py` - No changes; already works

---

## What Will Work After This Fix

✓ Select a file once  
✓ Database remembers the connection  
✓ Reload page → connection still there  
✓ Edit Excel file  
✓ Click Sync → reads latest version  
✓ No file picker reopens  
✓ No duplicates created  
✓ Windows Connector available for real background monitoring  

✗ Permanent browser access across sessions (impossible; browser security)  
→ But you CAN use Windows Connector for that

