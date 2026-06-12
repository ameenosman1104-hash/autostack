"""Central database — users and tenants."""
import sqlite3, os
from werkzeug.security import generate_password_hash

if os.environ.get("VERCEL"):
    MAIN_DB = "/tmp/main.db"
else:
    MAIN_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "main.db")
    os.makedirs(os.path.dirname(MAIN_DB), exist_ok=True)


def get_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_main_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            username     TEXT UNIQUE NOT NULL,
            email        TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            is_admin     INTEGER DEFAULT 0,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT (datetime('now'))
        );
    """)
    # Create default admin if none exists
    exists = conn.execute("SELECT id FROM tenants WHERE is_admin=1").fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO tenants (business_name, username, email, password_hash, is_admin) VALUES (?,?,?,?,1)",
            ("Admin", "admin", "admin@inventorytracker.com",
             generate_password_hash("admin123"))
        )
    conn.commit()
    conn.close()


def get_user_by_id(uid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tenants WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tenants WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_tenants():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tenants WHERE is_admin=0 ORDER BY business_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_tenant(business_name, username, email, password):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO tenants (business_name, username, email, password_hash) VALUES (?,?,?,?)",
            (business_name, username, email, generate_password_hash(password))
        )
        conn.commit()
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return tid, None
    except sqlite3.IntegrityError:
        conn.close()
        return None, "Username already exists."


def update_tenant(tid, **kwargs):
    conn = get_conn()
    if "password" in kwargs:
        kwargs["password_hash"] = generate_password_hash(kwargs.pop("password"))
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [tid]
    conn.execute(f"UPDATE tenants SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_tenant(tid):
    conn = get_conn()
    conn.execute("DELETE FROM tenants WHERE id=?", (tid,))
    conn.commit()
    conn.close()
