from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, UserMixin, current_user
from werkzeug.security import check_password_hash
from . import login_manager
from .main_db import get_user_by_id, get_user_by_username

auth_bp = Blueprint("auth", __name__)


class User(UserMixin):
    def __init__(self, data):
        self.id            = str(data["id"])
        self.username      = data["username"]
        self.business_name = data["business_name"]
        self.email         = data["email"]
        self.is_admin      = bool(data["is_admin"])
        self.is_active_acc = bool(data["is_active"])
        # Admin can impersonate a tenant
        self.tenant_id     = data.get("impersonate_id") or data["id"]


@login_manager.user_loader
def load_user(uid):
    data = get_user_by_id(int(uid))
    if not data:
        return None
    # Restore impersonation from session
    data["impersonate_id"] = session.get("impersonate_id")
    return User(data)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_data = get_user_by_username(username)
        if user_data and check_password_hash(user_data["password_hash"], password):
            if not user_data["is_active"]:
                flash("Account disabled. Contact your administrator.", "danger")
                return render_template("login.html")
            user = User(user_data)
            login_user(user)
            return redirect(url_for("dashboard.index"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        biz      = request.form.get("business_name", "").strip()
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm  = request.form.get("confirm_password", "").strip()

        if not biz or not username or not password:
            flash("Business name, username and password are required.", "danger")
            return render_template("register.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        from .main_db import create_tenant
        from .tenant_db import init_tenant_db
        tid, err = create_tenant(biz, username, email, password)
        if err:
            flash(err, "danger")
            return render_template("register.html")

        init_tenant_db(tid)
        flash("Account created! You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    session.pop("impersonate_id", None)
    logout_user()
    return redirect(url_for("auth.login"))
