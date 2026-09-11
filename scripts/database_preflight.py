"""Read-only schema report. Never imports the app or runs its auto-migrations."""
import argparse
import json
from pathlib import Path
import sqlite3

def inspect(path):
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={"database":path.name,"integrity":conn.execute("PRAGMA integrity_check").fetchone()[0]}
        if "debtors" in tables:
            result["debtor_count"]=conn.execute("SELECT COUNT(*) FROM debtors").fetchone()[0]
            cols={r[1] for r in conn.execute("PRAGMA table_info(debtors)")}
            result["missing_sync_fields"]=sorted({"due_date","sync_snapshot","sync_pending"}-cols)
        if "products" in tables:
            cols={r[1] for r in conn.execute("PRAGMA table_info(products)")}
            result["missing_product_fields"]=sorted({"updated_at","extra_data","deleted","deleted_at"}-cols)
        result["missing_dispatch_ledger"]="reminder_dispatch" not in tables
        return result

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("data_directory",type=Path)
    args=parser.parse_args()
    for path in sorted(args.data_directory.rglob("*.db")):
        print(json.dumps(inspect(path)))
