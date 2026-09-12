#!/usr/bin/env python3
"""
AutoStack Local File Connector
Monitors local Excel/CSV files and synchronizes with AutoStack.
Runs as a background service/daemon on Windows, macOS, or Linux.

Usage:
    python local_file_connector.py --setup            # Interactive setup
    python local_file_connector.py --pair URL TOKEN   # Pair with AutoStack
    python local_file_connector.py --start             # Start monitoring (daemon)
    python local_file_connector.py --status            # Show connection status
"""

import os
import sys
import json
import time
import hashlib
import argparse
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List


class LocalFileConnector:
    """Monitor a local file and sync with AutoStack via pairing token."""

    CONFIG_DIR = Path.home() / ".autostack-connector"
    CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
    LOG_FILE = CONFIG_DIR / "connector.log"

    def __init__(self):
        """Initialize connector with logging and config management."""
        self.CONFIG_DIR.mkdir(exist_ok=True)
        self._setup_logging()
        self.config = self._load_config()
        self.running = False

    def _setup_logging(self):
        """Configure logging to both file and console."""
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.LOG_FILE),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

    def _load_config(self) -> Dict:
        """Load credentials and configuration from disk."""
        if self.CREDENTIALS_FILE.exists():
            try:
                return json.loads(self.CREDENTIALS_FILE.read_text())
            except Exception as e:
                self.logger.error(f"Failed to load config: {e}")
                return {}
        return {}

    def _save_config(self, config: Dict):
        """Save credentials and configuration securely."""
        try:
            self.CREDENTIALS_FILE.write_text(json.dumps(config, indent=2))
            self.CREDENTIALS_FILE.chmod(0o600)
            self.logger.info("Configuration saved")
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")

    def setup_interactive(self):
        """Interactive setup: pair with AutoStack server."""
        print("\n=== AutoStack Local File Connector Setup ===\n")

        server_url = input("AutoStack server URL (e.g., https://autostack.example.com): ").strip()
        if not server_url.startswith(("http://", "https://")):
            print("Error: URL must start with http:// or https://")
            return

        file_path = input("Full path to Excel/CSV file (e.g., C:\\Users\\John\\Debtors.xlsx): ").strip()
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            print(f"Error: File not found: {file_path}")
            return

        if file_path_obj.suffix.lower() not in (".csv", ".xlsx"):
            print("Error: File must be CSV or XLSX format")
            return

        worksheet_name = None
        if file_path_obj.suffix.lower() == ".xlsx":
            worksheet_name = input("Worksheet name (leave blank for default): ").strip() or None

        pairing_token = input("Pairing token (from AutoStack Settings): ").strip()
        if not pairing_token:
            print("Error: Pairing token required")
            return

        device_name = input("Device name (e.g., 'John's Laptop'): ").strip() or "Unknown Device"

        # Verify connection to server
        try:
            headers = {"X-Pairing-Token": pairing_token, "Content-Type": "application/json"}
            test_resp = requests.post(
                f"{server_url}/api/local-file/status",
                headers=headers,
                timeout=5
            )
            if test_resp.status_code != 200:
                print(f"Error: Could not connect to server (HTTP {test_resp.status_code})")
                return
            print("✓ Connected to AutoStack server")
        except Exception as e:
            print(f"Error: Could not reach server: {e}")
            return

        config = {
            "server_url": server_url,
            "file_path": str(file_path_obj.absolute()),
            "worksheet_name": worksheet_name,
            "pairing_token": pairing_token,
            "device_name": device_name,
            "device_id": hashlib.sha256(device_name.encode()).hexdigest()[:16],
            "last_sync_at": None,
            "last_mtime_ns": None,
            "sync_interval_seconds": 15
        }

        self._save_config(config)
        self.config = config
        print(f"\n✓ Setup complete. Connector ready for {file_path_obj.name}")
        print(f"  Device: {device_name}")
        print(f"  Server: {server_url}")

    def pair_device(self, server_url: str, pairing_token: str):
        """Pair this device using a token from the server."""
        self.logger.info(f"Pairing with server: {server_url}")
        # Token is validated on upload; storage here is local
        # In production, validate token format/expiry on server
        pass

    def monitor_and_sync(self):
        """Main loop: monitor file for changes and sync."""
        if not self.config:
            self.logger.error("No configuration found. Run --setup first.")
            return

        self.running = True
        file_path = Path(self.config["file_path"])
        interval = self.config.get("sync_interval_seconds", 15)

        self.logger.info(f"Starting monitor for: {file_path}")
        self.logger.info(f"Sync interval: {interval}s")

        while self.running:
            try:
                if not file_path.exists():
                    self.logger.warning(f"File not found: {file_path}")
                    time.sleep(interval)
                    continue

                # Check if file has been modified
                current_mtime = file_path.stat().st_mtime_ns
                last_mtime = self.config.get("last_mtime_ns")

                if last_mtime and current_mtime == last_mtime:
                    # No change
                    time.sleep(interval)
                    continue

                self.logger.info("File changed detected, uploading snapshot...")

                # Read file and create snapshot
                snapshot = self._read_file_snapshot(file_path)
                if snapshot is None:
                    self.logger.error("Failed to read file snapshot")
                    time.sleep(interval)
                    continue

                # Upload snapshot
                success = self._upload_snapshot(snapshot)
                if success:
                    self.config["last_mtime_ns"] = current_mtime
                    self.config["last_sync_at"] = datetime.now().isoformat()
                    self._save_config(self.config)
                    self.logger.info(f"Sync successful: {len(snapshot)} rows")
                else:
                    self.logger.error("Upload failed")

                time.sleep(interval)

            except KeyboardInterrupt:
                self.logger.info("Shutdown requested")
                self.running = False
            except Exception as e:
                self.logger.error(f"Monitor loop error: {e}")
                time.sleep(interval)

    def _read_file_snapshot(self, file_path: Path) -> Optional[List[Dict]]:
        """Read and normalize file contents."""
        try:
            from file_snapshot import read_snapshot
            rows = read_snapshot(str(file_path), sheet=self.config.get("worksheet_name"))
            return rows
        except Exception as e:
            self.logger.error(f"Failed to read snapshot: {e}")
            return None

    def _upload_snapshot(self, snapshot: List[Dict]) -> bool:
        """Upload snapshot to AutoStack server."""
        try:
            url = f"{self.config['server_url']}/api/local-file/upload"
            headers = {
                "X-Pairing-Token": self.config["pairing_token"],
                "Content-Type": "application/json"
            }
            payload = {
                "snapshot": snapshot,
                "device_id": self.config["device_id"],
                "device_name": self.config["device_name"]
            }

            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                self.logger.error(f"Server error: HTTP {resp.status_code}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Upload failed: {e}")
            return False

    def show_status(self):
        """Display current connection status."""
        if not self.config:
            print("Not configured. Run --setup first.")
            return

        print("\n=== AutoStack Local File Connector Status ===\n")
        print(f"File: {self.config['file_path']}")
        print(f"Server: {self.config['server_url']}")
        print(f"Device: {self.config['device_name']}")
        print(f"Last sync: {self.config.get('last_sync_at', 'Never')}")
        print(f"Status: {'Running' if self.running else 'Stopped'}\n")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AutoStack Local File Connector - sync files with AutoStack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python local_file_connector.py --setup              # Interactive setup
  python local_file_connector.py --status             # Show status
  python local_file_connector.py --start              # Start monitoring
  python local_file_connector.py --pair URL TOKEN     # Advanced: pair manually
        """
    )

    parser.add_argument("--setup", action="store_true", help="Interactive setup")
    parser.add_argument("--start", action="store_true", help="Start monitoring (daemon)")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--pair", nargs=2, metavar=("URL", "TOKEN"), help="Pair with server (advanced)")

    args = parser.parse_args()

    connector = LocalFileConnector()

    if args.setup:
        connector.setup_interactive()
    elif args.start:
        connector.monitor_and_sync()
    elif args.status:
        connector.show_status()
    elif args.pair:
        connector.pair_device(args.pair[0], args.pair[1])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
