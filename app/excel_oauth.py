"""Microsoft Graph OAuth for Excel live sync via OneDrive/SharePoint."""
import os, time, sqlite3
from urllib.parse import urlsplit
from flask import Blueprint, current_app, session, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

bp = Blueprint("excel_oauth", __name__)

def configured():
    return all(current_app.config.get(k) for k in ("EXCEL_CLIENT_ID", "EXCEL_CLIENT_SECRET", "EXCEL_REDIRECT_URI"))

def init_excel_oauth(app):
    for key in ("EXCEL_CLIENT_ID", "EXCEL_CLIENT_SECRET", "EXCEL_REDIRECT_URI"):
        app.config[key] = os.environ.get(key, "")

    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)

    if app.config.get("EXCEL_CLIENT_ID"):
        oauth.register(
            "excel",
            client_id=app.config["EXCEL_CLIENT_ID"],
            client_secret=app.config["EXCEL_CLIENT_SECRET"],
            server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
            client_kwargs={
                "scope": "files.read offline_access",
                "access_type": "offline",
                "prompt": "consent"
            }
        )

    app.extensions["autostack_excel_oauth"] = oauth
    app.register_blueprint(bp)
    app.context_processor(lambda: {"excel_oauth_enabled": configured()})

def provider():
    return current_app.extensions.get("autostack_excel_oauth", {}).excel if configured() else None

def save_excel_connection(tid, connection_name, workbook_id, worksheet_name, table_name,
                          access_token, refresh_token, expires_in, graph_user_id,
                          column_mapping, unique_key_field):
    from .tenant_db import get_conn
    from .credential_store import encrypt_value

    expires_at = int(time.time()) + (expires_in or 3600) if expires_in else None

    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO live_excel_connections
               (connection_name, workbook_id, worksheet_name, table_name, access_token,
                refresh_token, expires_at, graph_user_id, column_mapping, unique_key_field, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (connection_name, workbook_id, worksheet_name, table_name,
             encrypt_value(access_token), encrypt_value(refresh_token) if refresh_token else None,
             expires_at, graph_user_id, column_mapping or "{}", unique_key_field or "Invoice No.")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()
        raise

def get_excel_connection(tid, connection_name=None):
    from .tenant_db import get_conn
    from .credential_store import decrypt_value

    conn = get_conn(tid)
    try:
        if connection_name:
            row = conn.execute(
                "SELECT * FROM live_excel_connections WHERE connection_name=? LIMIT 1",
                (connection_name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM live_excel_connections LIMIT 1"
            ).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "connection_name": row["connection_name"],
            "workbook_id": row["workbook_id"],
            "worksheet_name": row["worksheet_name"],
            "table_name": row["table_name"],
            "access_token": decrypt_value(row["access_token"]),
            "refresh_token": decrypt_value(row["refresh_token"]) if row["refresh_token"] else None,
            "expires_at": row["expires_at"],
            "graph_user_id": row["graph_user_id"],
            "column_mapping": row["column_mapping"],
            "unique_key_field": row["unique_key_field"],
            "last_sync_at": row["last_sync_at"],
            "last_sync_status": row["last_sync_status"],
            "last_sync_error": row["last_sync_error"]
        }
    except:
        conn.close()
        return None

def delete_excel_connection(tid, connection_name=None):
    from .tenant_db import get_conn

    conn = get_conn(tid)
    try:
        if connection_name:
            conn.execute("DELETE FROM live_excel_connections WHERE connection_name=?", (connection_name,))
        else:
            conn.execute("DELETE FROM live_excel_connections")
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()
        raise

@bp.route("/settings/excel/start")
@login_required
def start_oauth():
    if not configured():
        flash("Excel integration not configured.", "danger")
        return redirect(url_for("settings.index"))

    tid = current_user.tenant_id
    uri = current_app.config["EXCEL_REDIRECT_URI"]
    parsed = urlsplit(uri)

    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        flash("Excel OAuth configuration error.", "danger")
        return redirect(url_for("settings.index"))

    session["excel_oauth_tid"] = tid
    session["excel_oauth_state_time"] = time.time()

    try:
        return provider().authorize_redirect(uri)
    except Exception as e:
        current_app.logger.warning(f"Excel OAuth start failed: {e}")
        flash("Excel connection failed. Please try again.", "danger")
        return redirect(url_for("settings.index"))

@bp.route("/settings/excel/callback")
def excel_callback():
    if not configured():
        flash("Excel integration not configured.", "danger")
        return redirect(url_for("auth.login"))

    try:
        stored_time = session.pop("excel_oauth_state_time", 0)
        if time.time() - stored_time > 600:
            raise ValueError("OAuth state expired")

        tid = session.pop("excel_oauth_tid", None)
        if not tid:
            raise ValueError("OAuth state invalid")

        from flask_login import current_user
        if not current_user.is_authenticated or current_user.tenant_id != tid:
            raise ValueError("Tenant mismatch")

        token = provider().authorize_access_token()
        if not token or not token.get("access_token"):
            raise ValueError("No access token")

        # Store connection pending workbook selection
        session["excel_pending_token"] = {
            "access_token": token.get("access_token"),
            "refresh_token": token.get("refresh_token"),
            "expires_in": token.get("expires_in"),
            "graph_user_id": token.get("userinfo", {}).get("sub")
        }

        flash("Excel authorized. Select workbook and worksheet.", "success")
        return redirect(url_for("settings.index"))

    except Exception as e:
        current_app.logger.warning(f"Excel OAuth callback error: {e}")
        flash(f"Excel connection failed: {str(e)}", "danger")
        return redirect(url_for("settings.index"))

@bp.route("/settings/excel/status")
@login_required
def status():
    tid = current_user.tenant_id
    conn_data = get_excel_connection(tid)
    return jsonify({
        "connected": bool(conn_data),
        "connection_name": conn_data.get("connection_name") if conn_data else None,
        "workbook": f"{conn_data.get('workbook_id')} - {conn_data.get('worksheet_name')}" if conn_data else None,
        "last_sync": conn_data.get("last_sync_at") if conn_data else None,
        "last_status": conn_data.get("last_sync_status") if conn_data else None,
        "last_error": conn_data.get("last_sync_error") if conn_data else None
    })

@bp.route("/settings/excel/disconnect", methods=["POST"])
@login_required
def disconnect():
    tid = current_user.tenant_id
    try:
        delete_excel_connection(tid)
        flash("Excel disconnected.", "success")
    except Exception as e:
        flash("Failed to disconnect Excel.", "danger")
    return redirect(url_for("settings.index"))
