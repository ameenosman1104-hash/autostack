"""Google OpenID Connect. Existing accounts require explicit password-confirmed linking."""
import os
import secrets
import time
import sqlite3
from urllib.parse import urlsplit
from flask import Blueprint, current_app, session, redirect, url_for, flash, render_template, request
from flask_login import login_user
from werkzeug.security import check_password_hash, generate_password_hash
from .main_db import get_conn, get_user_by_id, get_user_by_username

bp = Blueprint("google_auth", __name__)

def configured():
    return all(current_app.config.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"))

def init_google(app):
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        app.config[key] = os.environ.get(key, "")
    # Additive identity mapping; existing account rows/passwords remain untouched.
    conn = get_conn()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS google_identities (subject TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
    finally:
        conn.close()
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    oauth.register("google", client_id=app.config["GOOGLE_CLIENT_ID"], client_secret=app.config["GOOGLE_CLIENT_SECRET"],
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})
    app.extensions["autostack_google"] = oauth
    app.register_blueprint(bp)
    app.context_processor(lambda: {"google_login_enabled": configured()})

def provider():
    return current_app.extensions["autostack_google"].google

def pending_identity():
    identity = session.get("google_pending")
    if not identity or identity.get("expires", 0) < time.time():
        session.pop("google_pending", None)
        return None
    return identity

def finish_login(user):
    if not user or not user["is_active"]:
        flash("This account is disabled. Contact your administrator.", "danger")
        return redirect(url_for("auth.login"))
    from .auth import User
    session.clear()  # Remove pending identity and any previous admin impersonation.
    login_user(User(user))
    return redirect(url_for("dashboard.index"))

@bp.route("/auth/google")
def start():
    if not configured():
        flash("Google sign-in is not configured yet. Please use your username and password.", "warning")
        return redirect(url_for("auth.login"))
    uri = current_app.config["GOOGLE_REDIRECT_URI"]
    parsed = urlsplit(uri)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        flash("Google sign-in configuration needs attention.", "danger")
        return redirect(url_for("auth.login"))
    session.pop("google_pending", None)
    try:
        return provider().authorize_redirect(uri, prompt="select_account")
    except Exception:
        current_app.logger.warning("Google authorization could not start")
        flash("Google sign-in is temporarily unavailable. Please try again.", "danger")
        return redirect(url_for("auth.login"))

@bp.route("/auth/google/callback")
def callback():
    if not configured():
        return redirect(url_for("auth.login"))
    try:
        # Authlib verifies state, nonce, signature, issuer, audience and token expiry.
        token = provider().authorize_access_token()
        info = token.get("userinfo")
        if not info or not token.get("id_token") or not isinstance(info.get("sub"), str) or not info["sub"] or info.get("email_verified") is not True or not info.get("email"):
            raise ValueError("Missing verified Google identity")
        conn = get_conn()
        try:
            linked = conn.execute("SELECT t.* FROM tenants t JOIN google_identities g ON t.id=g.tenant_id WHERE g.subject=?", (info["sub"],)).fetchone()
        finally:
            conn.close()
        if linked:
            return finish_login(dict(linked))
        session["google_pending"] = {"sub":info["sub"], "email":info["email"], "expires":time.time()+600}
        return redirect(url_for("google_auth.complete"))
    except Exception:
        session.pop("google_pending", None)
        current_app.logger.warning("Google sign-in callback rejected")
        flash("Google sign-in could not be verified. Please start again.", "danger")
        return redirect(url_for("auth.login"))

@bp.route("/auth/google/complete", methods=["GET", "POST"])
def complete():
    identity = pending_identity()
    if not identity:
        flash("Google verification expired. Please sign in with Google again.", "warning")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        # Reuse login throttling for password-confirmed linking.
        from .rate_limit import check_login_rate_limit, record_login_attempt
        conn = None
        try:
            action = request.form.get("action")
            if action == "link":
                username = request.form.get("username", "").strip()
                check_login_rate_limit(username)
                user = get_user_by_username(username)
                if not user or not user["is_active"] or not check_password_hash(user["password_hash"],request.form.get("password", "")):
                    record_login_attempt(username, False)
                    raise ValueError("Unable to link. Check your AutoStack username and password, and that your account is active.")
                tid = user["id"]
            elif action == "create":
                business = request.form.get("business_name", "").strip()
                username = request.form.get("username", "").strip()
                check_login_rate_limit(username)
                if not business or not username or len(business)>150 or len(username)>100:
                    raise ValueError("Enter a business name and username (maximum 150 and 100 characters).")
            else:
                raise ValueError("Choose whether to create or link an account.")
            conn = get_conn()
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM google_identities WHERE subject=?",(identity["sub"],)).fetchone():
                raise ValueError("This Google account is already connected. Start Google sign-in again.")
            if action == "create":
                if conn.execute("SELECT 1 FROM tenants WHERE LOWER(email)=LOWER(?)",(identity["email"],)).fetchone():
                    raise ValueError("An account already uses this email. Link your existing AutoStack account below.")
                cur=conn.execute("INSERT INTO tenants(business_name,username,email,password_hash) VALUES(?,?,?,?)",(business,username,identity["email"],generate_password_hash(secrets.token_urlsafe(48))))
                tid=cur.lastrowid
                from .tenant_db import init_tenant_db
                init_tenant_db(tid)
            else:
                active=conn.execute("SELECT is_active FROM tenants WHERE id=?",(tid,)).fetchone()
                if not active or not active[0]: raise ValueError("This account is disabled.")
            conn.execute("INSERT INTO google_identities(subject,tenant_id) VALUES(?,?)",(identity["sub"],tid))
            conn.commit()
            # Record successful account setup/linking via Google
            if action == "link":
                record_login_attempt(username, True)
            elif action == "create":
                record_login_attempt(username, True)
            return finish_login(get_user_by_id(tid))
        except ValueError as error:
            if conn: conn.rollback()
            flash(str(error), "danger")
        except sqlite3.IntegrityError:
            if conn: conn.rollback()
            flash("That username or account is already connected. Use another username or sign in to the existing account.", "danger")
        except Exception:
            if conn: conn.rollback()
            current_app.logger.warning("Google account setup failed")
            flash("Account setup could not be completed. Please try again or contact support.", "danger")
        finally:
            if conn: conn.close()
    return render_template("google_complete.html", google_email=identity["email"])
