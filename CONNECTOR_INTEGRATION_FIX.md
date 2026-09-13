# Saved Source Synchronization Fix - Analysis & Implementation Plan

## Problem Identified

**Symptom:** Excel file appears under "Saved Connections" but clicking Sync opens the file picker again.

**Root Cause:** The sync endpoint (`/api/data-sources/sync-now`) explicitly rejects local file connections:
```python
if source_type == "local_file":
    return jsonify({
        "error": "Local file sync is managed by the Windows connector",
        "note": "Ensure the connector is running on the target computer"
    }), 400
```

**Why This Happens:**
1. Browser file input can't persist file access across sessions
2. Windows connector is designed for background monitoring
3. But there's no UI to set up or manage the connector
4. Users see "Saved Connections" but can't actually use them

## Current Architecture

```
User Selects File
    ↓
Saved to data_source_connections table
    ↓
Sync Now Dialog Shows in "Saved Connections"
    ↓
User Clicks Sync
    ↓
Tries to sync via API endpoint
    ↓
API rejects local files
    ↓
User sees error: "Use Windows connector"
    ↓
But no UI to set up or launch connector
```

## Required Solution

### For Local Excel/CSV Files
**Two Modes:**

#### Mode 1: Connector Mode (Background Automatic)
- Windows connector monitors file
- Syncs every ~15 seconds automatically
- Works when browser is closed
- Requires: Connector installed and running

#### Mode 2: Browser Mode (On-Demand)
- User clicks "Sync Now" in browser
- Requires file to be selected (due to browser security)
- Works immediately, no setup needed
- Limited by: File permissions and browser capabilities

### For APIs
- Save endpoint, auth, mapping, pagination
- Poll automatically every 15 seconds
- Or use webhooks if supported
- No re-configuration needed

## Implementation Requirements

### 1. Connector Management UI
**New UI Component in Sync Now Dialog:**
```
┌─────────────────────────────────────┐
│ Saved Connections                   │
├─────────────────────────────────────┤
│ ✓ Main Debtors File (Connector)     │
│   Status: Connected & Monitoring    │
│   Last sync: 2 minutes ago          │
│   [Sync Now] [Pause] [Change] [✕]  │
│                                     │
│ ⚠ Stock Feed (Connector - Offline)  │
│   Status: Connector not running     │
│   [Launch Connector] [Change] [✕]  │
│                                     │
│ ✓ API: ERP System                   │
│   Status: Auto-syncing              │
│   Last sync: 1 minute ago           │
│   [Sync Now] [Change] [✕]          │
└─────────────────────────────────────┘
```

### 2. Connector Setup Integration
**When user selects a local file:**
1. Generate pairing token
2. Save connection with pairing token
3. Show: "Setup Instructions for Windows Connector"
4. Provide easy launcher/download

**Connector Status Check:**
- Ping connector at `/connector/status` endpoint
- Show: Connected, Offline, or Never Started

### 3. Database Schema Updates
**Add to data_source_connections:**
```sql
connector_mode BOOLEAN DEFAULT false  -- true = use connector, false = browser
pairing_token_hash TEXT               -- Hash of token for connector auth
last_connector_contact DATETIME       -- When connector last checked in
connector_status TEXT                 -- 'connected', 'offline', 'never_started'
```

### 4. API Endpoints
**New endpoints needed:**
```
GET /api/connectors/status/{token}    -- Check if connector is running
POST /api/connectors/setup             -- Generate pairing token
POST /api/connections/{id}/pause       -- Pause auto-sync
POST /api/connections/{id}/resume      -- Resume auto-sync
POST /api/connections/{id}/change-source  -- Change file/API without removing data
```

### 5. Connector Integration
**Modify existing connector to:**
- Send heartbeat to AutoStack every 15 seconds
- Report file changes detected
- Show connection status in UI
- Auto-retry on network failures

## Implementation Steps

### Phase 1: Connector Status Display (Critical)
1. Add connector_status field to schema
2. Show in UI: "Connector: Connected" or "Connector: Offline"
3. When offline: Show "Launch Connector" button

### Phase 2: Connector Setup (Critical)
1. Generate pairing tokens for local file connections
2. Provide clear setup instructions
3. Link to connector download/launcher
4. Show connection verification step

### Phase 3: Auto-Sync for APIs (Important)
1. Add background job to poll APIs
2. Sync every 15 seconds (configurable)
3. Respect rate limits
4. Show sync status in UI

### Phase 4: UI Polish (Nice-to-have)
1. Pause/Resume buttons
2. Change Source dialog
3. Sync history/logs
4. Error notifications

## Honest Browser Limitations

**What We CAN Do:**
- Show saved connection was used before
- For APIs: Auto-sync in background
- For connector: Show status and launch button

**What We CANNOT Do (Browser Limitation):**
- Persist file read access without user action
- Read local files after browser closes
- Access files without explicit user permission per session

**Solution: Two Modes**
1. **For Local Files:** Use Windows connector (works with browser closed)
2. **For Browser:** Show "Upload File" for one-time syncs

## Acceptance Criteria

✓ **Connect a file once**
- User selects Excel file
- System saves pairing token
- Shows "Ready for Connector"

✓ **Connector setup**
- UI clearly instructs how to set up
- Provides launcher/download
- Shows status (Connected/Offline)

✓ **Automatic sync**
- Connector monitors file
- Syncs without browser action
- Shows "Last synced: 2 minutes ago"

✓ **Change source**
- User can change which file without losing data
- Preview before applying
- Keeps all imported records

✓ **Remove connection**
- Removes only the connection
- Preserves all imported data
- Stops connector monitoring

## Files That Need Changes

1. **app/templates/inventory.html** - Enhanced Saved Connections display
2. **app/templates/debtors.html** - Same as above for debtors
3. **app/routes/data_sources_api.py** - Add connector status endpoints
4. **app/db_migrations.py** - Migration for connector fields
5. **app/services/auto_sync.py** - Add API polling job
6. **connector/local_file_connector.py** - Add heartbeat/status reporting

## Time Estimate (if implementing full solution)
- Phase 1 (Status): 2 hours
- Phase 2 (Setup): 3 hours  
- Phase 3 (Auto-sync): 2 hours
- Phase 4 (Polish): 2 hours

## Critical Fix (Minimum)
To make existing "Saved Connections" work:
1. Add status display (Offline/Monitoring)
2. Add "Launch Connector" button
3. Provide setup instructions
4. Document pairing process

This makes the current implementation honest about what's happening instead of showing non-functional saved connections.
