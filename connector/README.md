# AutoStack Local File Connector

Automatically synchronize changes from a local Excel or CSV file to AutoStack without manual imports. The connector runs as a background process on your computer and detects file changes every 15 seconds.

## Features

- **Background monitoring**: Runs silently while you work
- **Automatic detection**: Recognizes when you save changes to your file
- **Email-only updates**: Syncs just an email change without re-importing everything
- **Conflict resolution**: Flags conflicts when the same record is edited in both places
- **Secure pairing**: Device-to-server connection via revocable pairing tokens
- **Audit trail**: Logs all changes for compliance and troubleshooting
- **Windows/Mac/Linux**: Works on any platform with Python

## Installation

### Prerequisites

- Python 3.8 or later
- AutoStack server running with local file sync enabled
- An Excel (.xlsx) or CSV (.csv) file with at least "Invoice No." and "Name" columns

### Step 1: Install Connector

```bash
# On Windows
cd C:\Users\YourName\AppData\Local\AutoStack
pip install -r requirements.txt
python local_file_connector.py --setup

# On macOS/Linux
cd ~/.autostack-connector
pip install -r requirements.txt
python local_file_connector.py --setup
```

### Step 2: Get Pairing Token from AutoStack

1. Log into AutoStack
2. Go to **Settings → Local File Sync**
3. Click **"Add Local File Connection"**
4. Enter:
   - Connection name (e.g., "Debtors Spreadsheet")
   - File path on your computer
   - Click **"Generate Pairing Token"**
5. **Copy the token** (you'll only see it once)

### Step 3: Run Interactive Setup

```bash
python local_file_connector.py --setup
```

Answer the prompts:
- **AutoStack URL**: `https://your-autostack-server.com`
- **File path**: Full path to your Excel/CSV file
- **Worksheet name** (Excel only): Leave blank if your file has one sheet
- **Pairing token**: Paste the token from step 2
- **Device name**: A friendly name like "John's Laptop"

The setup will test the connection and save your configuration.

## Usage

### Start Monitoring (Normal)

```bash
python local_file_connector.py --start
```

The connector will run indefinitely, monitoring your file for changes every 15 seconds.

### View Status

```bash
python local_file_connector.py --status
```

Shows last sync time, connected server, and file location.

### Stop Monitoring

Press `Ctrl+C` in the terminal where the connector is running.

## Running at Startup

### Windows (Using Task Scheduler)

1. Open **Task Scheduler**
2. Click **Create Basic Task**
3. Name: `AutoStack File Connector`
4. Trigger: **At log on**
5. Action:
   - Program: `C:\Python39\python.exe` (or your Python path)
   - Arguments: `C:\path\to\connector\local_file_connector.py --start`
   - Start in: `C:\path\to\connector`
6. Click **Finish**

To check if the task runs, look at the log:

```bash
type %USERPROFILE%\.autostack-connector\connector.log
```

### macOS (Using LaunchAgent)

Create `~/Library/LaunchAgents/com.autostack.connector.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.autostack.connector</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>~/autostack-connector/local_file_connector.py</string>
        <string>--start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>~/.autostack-connector/connector.log</string>
    <key>StandardOutPath</key>
    <string>~/.autostack-connector/connector.log</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.autostack.connector.plist
```

### Linux (Using systemd)

Create `/etc/systemd/user/autostack-connector.service`:

```ini
[Unit]
Description=AutoStack Local File Connector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/autostack-connector/local_file_connector.py --start
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user enable autostack-connector
systemctl --user start autostack-connector
systemctl --user status autostack-connector
```

## File Format Requirements

Your file must have these columns (case-insensitive):
- **Invoice No.** (required) - Unique identifier, used to match existing records
- **Name** (required) - Customer/debtor name

Optional columns (auto-detected):
- **Email** / **E-mail** / **Email Address** / **Contact Email**
- **Amount** / **Invoice Amount** / **Original Amount**
- **Paid** / **Amount Paid**
- **Invoice Date** / **Purchase Date** (format: DD/MM/YYYY or YYYY-MM-DD)
- **Due Date**

## How It Works

1. **File monitoring**: Every 15 seconds, the connector checks if your file's modification time has changed
2. **Snapshot creation**: If changed, it reads the file and validates all rows
3. **Upload**: Sends the snapshot to AutoStack with your pairing token
4. **Matching**: AutoStack matches records by Invoice No. (or configured unique field)
5. **Change detection**: Identifies what's new, changed, or removed
6. **Conflict handling**: If a record was edited in both places, it's flagged for your review
7. **Application**: Changes are applied to the Debtors table
8. **Preservation**: Custom reminders, manual edits, and payment history are kept intact

## Conflict Resolution

If a record is edited in both AutoStack and your file between syncs:

1. The connector flags it as a conflict
2. You'll see a **"Conflicts"** section in AutoStack Settings
3. For each conflict, choose:
   - **Keep AutoStack** - Your spreadsheet change is ignored
   - **Use Source** - Your spreadsheet change overwrites AutoStack
4. Resolve, and the next sync will apply your choice

## Troubleshooting

### "File not found"
- Check the file path in the configuration
- Ensure the file exists and hasn't been moved
- Update with `python local_file_connector.py --setup`

### "Could not connect to server"
- Verify the AutoStack URL is correct
- Check your internet connection
- Ensure the pairing token hasn't expired
- Revoke and generate a new token in AutoStack Settings

### "Sync failed: HTTP 401"
- Your pairing token has been revoked
- Generate a new token in AutoStack Settings
- Run setup again with the new token

### "Invalid file format"
- Check that your file has "Invoice No." and "Name" columns
- Ensure the file isn't corrupted or still being saved
- Try saving the file again and waiting 30 seconds

### Check Log File

All activity is logged. View it with:

```bash
# Windows
type %USERPROFILE%\.autostack-connector\connector.log

# macOS/Linux
tail -f ~/.autostack-connector/connector.log
```

## Security & Privacy

- **Pairing token**: Hashed on the server; your device stores only the plaintext version
- **File contents**: Only sent to AutoStack when you save changes (not continuously scanned)
- **Credentials**: Stored in encrypted format on your computer (read-only to your user)
- **Revocation**: Instantly disable any device from AutoStack Settings
- **Audit trail**: All syncs are logged and available for review

## Uninstall

1. Disconnect in AutoStack Settings → Local File Sync (or wait 30 days for auto-revocation)
2. Stop the connector:
   ```bash
   # Windows Task Scheduler
   taskkill /IM python.exe /F   # If running in background
   
   # macOS LaunchAgent
   launchctl unload ~/Library/LaunchAgents/com.autostack.connector.plist
   
   # Linux systemd
   systemctl --user stop autostack-connector
   systemctl --user disable autostack-connector
   ```
3. Delete the configuration directory:
   ```bash
   # Windows
   rmdir /s %USERPROFILE%\.autostack-connector
   
   # macOS/Linux
   rm -rf ~/.autostack-connector
   ```

## Support

For issues or questions, contact support@autostack.local or visit our help center.

---

**Latest version**: 1.0.0  
**Requires**: AutoStack 2.0+, Python 3.8+  
**License**: Proprietary (AutoStack)
