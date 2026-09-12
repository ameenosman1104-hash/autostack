"""Gmail OAuth 2.0 integration for sending reminders without App Password.

Separate from Google login - users can use Google login without granting Gmail sending access,
and vice versa. Tokens are encrypted and stored per-tenant.
"""
import os
import time
import sqlite3
from urllib.parse import urlsplit
from flask import Blueprint, current_app, session, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

bp = Blueprint("gmail_oauth", __name__)

def configured():
    """Check if Gmail OAuth is configured in environment."""
    return all(current_app.config.get(k) for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REDIRECT_URI"))

def init_gmail_oauth(app):
    """Initialize Gmail OAuth client."""
    for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REDIRECT_URI"):
        app.config[key] = os.environ.get(key, "")

    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)

    if app.config.get("GMAIL_CLIENT_ID"):
        oauth.register(
            "gmail",
            client_id=app.config["GMAIL_CLIENT_ID"],
            client_secret=app.config["GMAIL_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={
                "scope": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email openid",
                "code_challenge_method": "S256",
                "access_type": "offline",  # Request refresh token
                "prompt": "consent"  # Always show consent to get refresh token
            }
        )

    app.extensions["autostack_gmail_oauth"] = oauth
    app.register_blueprint(bp)
    app.context_processor(lambda: {"gmail_oauth_enabled": configured()})

def provider():
    """Get Gmail OAuth provider."""
    return current_app.extensions.get("autostack_gmail_oauth", {}).gmail if configured() else None

def get_gmail_token(tid):
    """Get Gmail OAuth token for a tenant (decrypted)."""
    from .tenant_db import get_conn
    from .credential_store import decrypt_value

    conn = get_conn(tid)
    try:
        row = conn.execute(
            "SELECT access_token, refresh_token, expires_at FROM gmail_oauth_tokens LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "access_token": decrypt_value(row[0]),
            "refresh_token": decrypt_value(row[1]) if row[1] else None,
            "expires_at": row[2],
            "authorized_email": row[0]  # Will be decrypted if needed
        }
    except Exception as e:
        conn.close()
        return None

def save_gmail_token(tid, authorized_email, access_token, refresh_token, expires_in):
    """Save encrypted Gmail OAuth token for a tenant."""
    from .tenant_db import get_conn
    from .credential_store import encrypt_value

    expires_at = int(time.time()) + (expires_in or 3600) if expires_in else None

    conn = get_conn(tid)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO gmail_oauth_tokens
               (authorized_email, access_token, refresh_token, expires_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (authorized_email, encrypt_value(access_token),
             encrypt_value(refresh_token) if refresh_token else None, expires_at)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.close()
        raise

def delete_gmail_token(tid):
    """Delete Gmail OAuth token for a tenant."""
    from .tenant_db import get_conn

    conn = get_conn(tid)
    try:
        conn.execute("DELETE FROM gmail_oauth_tokens")
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()

def get_authorized_email(tid):
    """Get the authorized Gmail email address for a tenant."""
    from .tenant_db import get_conn

    conn = get_conn(tid)
    try:
        row = conn.execute(
            "SELECT authorized_email FROM gmail_oauth_tokens LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except:
        conn.close()
        return None

def has_gmail_token(tid):
    """Check if a tenant has Gmail OAuth connected."""
    return bool(get_authorized_email(tid))

@bp.route("/settings/gmail-oauth/start")
@login_required
def start_oauth():
    """Start Gmail OAuth flow."""
    if not configured():
        flash("Gmail OAuth is not configured.", "danger")
        return redirect(url_for("settings.index"))

    tid = current_user.tenant_id
    uri = current_app.config["GMAIL_REDIRECT_URI"]
    parsed = urlsplit(uri)

    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        flash("Gmail OAuth configuration error.", "danger")
        return redirect(url_for("settings.index"))

    # Store tenant_id in session for verification in callback
    session["gmail_oauth_tid"] = tid
    session["gmail_oauth_state_time"] = time.time()

    try:
        return provider().authorize_redirect(uri)
    except Exception as e:
        current_app.logger.warning(f"Gmail OAuth start failed: {e}")
        flash("Gmail OAuth connection failed. Please try again.", "danger")
        return redirect(url_for("settings.index"))

@bp.route("/settings/gmail-oauth/callback")
def gmail_callback():
    """Gmail OAuth callback."""
    if not configured():
        flash("Gmail OAuth is not configured.", "danger")
        return redirect(url_for("auth.login"))

    try:
        # Verify state is recent and matches stored tenant
        stored_time = session.pop("gmail_oauth_state_time", 0)
        if time.time() - stored_time > 600:  # 10 minute expiry
            raise ValueError("OAuth state expired. Please try again.")

        tid = session.pop("gmail_oauth_tid", None)
        if not tid:
            raise ValueError("OAuth state invalid. Please try again.")

        # Verify current user's tenant matches the one that started the flow
        from flask_login import current_user
        if not current_user.is_authenticated or current_user.tenant_id != tid:
            raise ValueError("Tenant mismatch. You may have been logged out.")

        # Exchange code for token
        token = provider().authorize_access_token()
        if not token or not token.get("access_token"):
            raise ValueError("No access token received")

        # Get user info to verify email
        user_info = token.get("userinfo", {})
        authorized_email = user_info.get("email")

        if not authorized_email:
            raise ValueError("Could not verify Gmail address")

        # Save encrypted token
        save_gmail_token(
            tid,
            authorized_email,
            token.get("access_token"),
            token.get("refresh_token"),
            token.get("expires_in")
        )

        flash(f"Gmail connected: {authorized_email}", "success")
        return redirect(url_for("settings.index"))

    except Exception as e:
        current_app.logger.warning(f"Gmail OAuth callback error: {e}")
        flash(f"Gmail connection failed: {str(e)}", "danger")
        return redirect(url_for("settings.index"))

@bp.route("/settings/gmail-oauth/disconnect", methods=["POST"])
@login_required
def disconnect():
    """Disconnect Gmail OAuth."""
    tid = current_user.tenant_id
    try:
        delete_gmail_token(tid)
        flash("Gmail disconnected.", "success")
    except Exception as e:
        flash("Failed to disconnect Gmail.", "danger")
    return redirect(url_for("settings.index"))

@bp.route("/settings/gmail-oauth/status")
@login_required
def status():
    """Get Gmail OAuth connection status."""
    tid = current_user.tenant_id
    email = get_authorized_email(tid)
    return jsonify({"connected": bool(email), "email": email})
