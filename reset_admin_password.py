#!/usr/bin/env python3
"""Reset admin password for Autostack."""
import sqlite3
import os
import sys
from werkzeug.security import generate_password_hash

# Find database
db_path = None
try:
    if os.path.exists("/tmp/main.db"):
        db_path = "/tmp/main.db"
    else:
        local_path = os.path.join(os.path.dirname(__file__), "data", "main.db")
        if os.path.exists(local_path):
            db_path = local_path
except:
    pass

if not db_path or not os.path.exists(db_path):
    print("[!] Database not found. Make sure Autostack has been run at least once.")
    exit(1)

# Get password from argument or prompt
if len(sys.argv) > 1:
    new_password = sys.argv[1]
else:
    new_password = input("Enter new admin password (min 6 chars): ").strip()

if len(new_password) < 6:
    print("[!] Password must be at least 6 characters.")
    exit(1)

# Update database
try:
    conn = sqlite3.connect(db_path)
    password_hash = generate_password_hash(new_password)
    result = conn.execute(
        "UPDATE tenants SET password_hash=? WHERE is_admin=1",
        (password_hash,)
    )
    conn.commit()

    if result.rowcount > 0:
        print("[OK] Admin password reset successfully!")
        print("[*] You can now login with username: admin")
    else:
        print("[!] No admin account found.")

    conn.close()
except Exception as e:
    print(f"[!] Error: {e}")
    exit(1)
