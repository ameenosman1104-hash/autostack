# AutoStack Local File Connector - Setup Guide

This guide walks through setting up real-time synchronization between a local Excel/CSV file and AutoStack.

## Overview

The Local File Connector monitors a file on your computer and automatically uploads changes to AutoStack every 15 seconds. It works while your browser is closed and survives computer restarts.

**Architecture:**
- **Web UI** (AutoStack Settings) → Manage connections, resolve conflicts
- **Windows Connector** (background service) → Monitors file, uploads snapshots
- **Secure pairing** → Device-specific revocable tokens
- **Audit trail** → All changes logged with timestamps

## Step 1: Install the Connector

### On Windows

Download the connector from your AutoStack Settings page or install manually:

```powershell
# Create installation directory
mkdir "$env:USERPROFILE\AppData\Local\AutoStackConnector"
cd "$env:USERPROFILE\AppData\Local\AutoStackConnector"

# Download connector files (or copy from git)
# - local_file_connector.py
# - file_snapshot.py
# - requirements.txt

# Install dependencies
pip install -r requirements.txt
```

### On macOS

```bash
mkdir -p ~/.autostack-connector
cd ~/.autostack-connector

# Copy connector files
# - local_file_connector.py
# - file_snapshot.py
# - requirements.txt

pip3 install -r requirements.txt
```

### On Linux

```bash
mkdir -p ~/.autostack-connector
cd ~/.autostack-connector

# Copy connector files
pip3 install -r requirements.txt
```

## Step 2: Prepare Your File

Your Excel or CSV file must have at least these columns:
- **Invoice No.** (or equivalent) - Unique identifier
- **Name** (or Customer, Customer Name) - Debtor/customer name

Optional columns (auto-detected):
- Email, E-mail, Email Address, Contact Email
- Amount, Invoice Amount, Original Amount
- Amount Paid, Paid
- Invoice Date, Purchase Date (DD/MM/YYYY or YYYY-MM-DD)
- Due Date

**Note:** Save your file to a permanent location before pairing (not Temp or Downloads).

## Step 3: Create Connection in AutoStack

1. Log into AutoStack
2. Go to **Settings → Local File Sync**
3. Click **Add Connection**
4. Enter:
   - **Name:** A friendly name (e.g., "Main Debtors")
   - **File Path:** Full path to your file (e.g., `C:\Users\John\Debtors.xlsx`)
   - **Worksheet:** Leave blank if single sheet, otherwise enter sheet name
5. Click **Preview** to verify columns are detected
6. Click **Generate Connection** → A pairing token is created
7. **Important:** Copy and save the pairing token securely

## Step 4: Run Connector Setup

```powershell
# Windows
python "$env:USERPROFILE\AppData\Local\AutoStackConnector\local_file_connector.py" --setup
```

The setup wizard will ask:
- AutoStack server URL (e.g., `https://autostack.example.com`)
- Path to your file
- Worksheet name (Excel only)
- Pairing token from Step 3
- Device name (e.g., "John's Laptop")

A test connection will be made to verify everything works.

## Step 5: Start the Connector

### Manual (for testing)

```powershell
python "$env:USERPROFILE\AppData\Local\AutoStackConnector\local_file_connector.py" --start
```

You'll see messages like:
```
2026-01-15 14:30:45 [INFO] Starting monitor for: C:\Users\John\Debtors.xlsx
2026-01-15 14:30:45 [INFO] Sync interval: 15s
```

Press `Ctrl+C` to stop.

### Automatic (at Windows startup)

#### Option A: Task Scheduler (Recommended)

1. Open **Task Scheduler**
2. Right-click → **Create Basic Task**
3. **General:**
   - Name: `AutoStack Local File Connector`
   - Description: `Sync local Excel files with AutoStack`
   - Check: "Run with highest privileges"
4. **Trigger:**
   - Begin the task: **At log on**
5. **Action:**
   - Action: **Start a program**
   - Program/script: `C:\Python39\python.exe` (find your Python path)
   - Arguments: `C:\Users\John\AppData\Local\AutoStackConnector\local_file_connector.py --start`
   - Start in: `C:\Users\John\AppData\Local\AutoStackConnector`
6. **Conditions:**
   - Uncheck "Start the task only if the computer is on AC power"
7. Click **Finish**

To verify it runs:
- Restart your computer
- Check log file: `%USERPROFILE%\.autostack-connector\connector.log`

#### Option B: Windows Batch Script

Create `start-connector.bat`:

```batch
@echo off
cd /d "%USERPROFILE%\AppData\Local\AutoStackConnector"
python local_file_connector.py --start
pause
```

Then create a shortcut to this batch file in:
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`

## Step 6: Test the Connection

1. Make a test change in your Excel file:
   - Add a new row, or
   - Update an existing email address, or
   - Change an amount
2. Save the file
3. Wait up to 30 seconds
4. Check AutoStack to see if the change appeared

If nothing happens:
- Check the log: `%USERPROFILE%\.autostack-connector\connector.log`
- Verify the connector is running: `python ... --status`
- Ensure the file path is correct

## Managing Connections

### View Status

In AutoStack Settings → Local File Sync:
- **Connected** = Device is paired and active
- **Last sync:** Timestamp of last successful sync
- **Status:** Latest result (success, error, pending)
- **Error message:** If last sync failed

### Resolve Conflicts

If the same record is edited in both AutoStack and your file:
1. A **Conflict** appears in Settings
2. Choose:
   - **Keep AutoStack** = Your file change is ignored
   - **Use Source** = Your file change overwrites AutoStack
3. Click **Resolve**
4. Next sync applies your choice

### Disable a Device

To stop a device from syncing:
1. Go to Settings → Local File Sync
2. Click **Revoke** next to the connection
3. Device can no longer upload

To re-enable: Generate a new pairing token and run setup again.

## What Gets Synced

| Field | Behavior |
|-------|----------|
| Name | Updated from file |
| Email | Updated from file; blank file value preserves AutoStack email |
| Amount Owed | Set for NEW debtors only; existing debtors: not updated |
| Amount Paid | Tracked separately; incremental payments |
| Purchase Date | Updated |
| Due Date | Updated |
| Custom Reminders | Preserved; sync doesn't change reminder settings |
| Payment History | Preserved; existing payments not removed |
| Status | Recalculated based on balance and due date |

## Troubleshooting

### "File not found"
- Verify file still exists at configured path
- Move file, update path, run setup again: `--setup`

### "Could not connect to server"
- Check AutoStack URL is correct
- Verify internet connection
- Ensure pairing token hasn't expired (revoke and generate new one)

### "Sync failed: HTTP 401"
- Token has been revoked
- Generate new pairing token in Settings
- Run setup again

### "Duplicate debtors created"
- Verify "Invoice No." column is properly detected
- Check for blank or duplicate invoice IDs in your file
- Remove duplicates and re-sync

### "Changes not syncing"
- Check log file: `type %USERPROFILE%\.autostack-connector\connector.log`
- Verify connector is running: `python ... --status`
- Ensure you're saving the file (not just editing)
- Check timestamp in "Last sync" field in Settings

### "Email never syncs"
- Verify your file has an Email column with correct header
- First sync maps email column; subsequent syncs use that mapping
- If column not detected, restart connector

## Logs and Debugging

All activity is logged to: `%USERPROFILE%\.autostack-connector\connector.log`

View recent logs:

```powershell
Get-Content "$env:USERPROFILE\.autostack-connector\connector.log" -Tail 50
```

Or follow in real-time:

```powershell
Get-Content "$env:USERPROFILE\.autostack-connector\connector.log" -Wait
```

## Uninstall

1. Stop the connector:
   - Task Scheduler: Disable the task
   - Or: Kill python.exe from Task Manager

2. Revoke in AutoStack Settings → Local File Sync (or let it auto-revoke after 30 days)

3. Delete connector directory:
   ```powershell
   Remove-Item -Recurse "$env:USERPROFILE\AppData\Local\AutoStackConnector"
   Remove-Item -Recurse "$env:USERPROFILE\.autostack-connector"
   ```

## Security & Privacy

- **Pairing Token:** Hashed on server; device stores only plaintext
- **File Integrity:** Snapshot validated before upload; prevents injection
- **TLS/HTTPS:** All communication encrypted; no passwords sent
- **Device Identification:** Each device has unique ID; revoking is instant
- **Audit Trail:** Every change logged with timestamp and source

## Advanced: Multiple Files

To monitor multiple files:
1. Install connector once (shared installation)
2. Create separate pairing for each file in Settings
3. Run multiple connector instances:
   ```powershell
   # Terminal 1
   python local_file_connector.py --pair URL TOKEN1 --start

   # Terminal 2
   python local_file_connector.py --pair URL TOKEN2 --start
   ```

Or use separate configurations:
```powershell
$env:AUTOSTACK_CONFIG_FILE = "C:\Users\John\config-debtors.json"
python local_file_connector.py --setup
```

## Support

For issues or feature requests:
- Check the log file first
- Visit Settings → Help → "Local File Connector"
- Contact: support@autostack.local
- Email: support@autostack.local with your log file attached

---

**Version:** 1.0  
**Last Updated:** 2026-01-15  
**Requires:** AutoStack 2.0+, Python 3.8+
