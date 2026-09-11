"""Migrate existing production databases with backups and integrity verification."""
import sys
import sqlite3
import os
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db_migrations import migrate_tenant_db, verify_backup_restoration


def migrate_all_databases(data_dir):
    """Apply migrations to all tenant databases in data directory."""
    data_path = Path(data_dir)
    tenants_dir = data_path / "tenants"

    if not tenants_dir.exists():
        print(f"No tenants directory found at {tenants_dir}")
        return 0

    db_files = list(tenants_dir.glob("*.db"))
    if not db_files:
        print(f"No databases found in {tenants_dir}")
        return 0

    print(f"Found {len(db_files)} database(s) to migrate")
    print()

    successful = 0
    failed = 0
    backed_up = 0

    for db_path in sorted(db_files):
        tenant_id = db_path.stem
        print(f"Migrating tenant {tenant_id}...", end=" ", flush=True)

        try:
            backup = migrate_tenant_db(tenant_id, str(db_path))
            if backup:
                backed_up += 1
                print(f"OK (backup: {Path(backup).name})")
            else:
                print("OK")

            # Verify integrity after migration
            conn = sqlite3.connect(str(db_path))
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()

            if result != "ok":
                print(f"  WARNING: Integrity check failed: {result}")
                failed += 1
            else:
                successful += 1

        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1

    print()
    print(f"Results:")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print(f"  With backups: {backed_up}")
    print()

    if failed > 0:
        print("Some migrations failed. Review errors above.")
        return 1

    print("All databases migrated successfully.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_databases.py <data_directory>")
        sys.exit(1)

    data_dir = sys.argv[1]
    sys.exit(migrate_all_databases(data_dir))
