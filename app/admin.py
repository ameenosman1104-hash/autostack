from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from .main_db import get_all_tenants, create_tenant, update_tenant, delete_tenant, get_user_by_id
from .tenant_db import init_tenant_db
import os

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/")
@login_required
@admin_required
def index():
    tenants = get_all_tenants()
    return render_template("admin/tenants.html", tenants=tenants)


@admin_bp.route("/tenants/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_tenant():
    if request.method == "POST":
        biz   = request.form.get("business_name", "").strip()
        user  = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        pwd   = request.form.get("password", "").strip()
        if not biz or not user or not pwd:
            flash("Business name, username, and password are required.", "danger")
            return render_template("admin/new_tenant.html")
        tid, err = create_tenant(biz, user, email, pwd)
        if err:
            flash(err, "danger")
            return render_template("admin/new_tenant.html")
        init_tenant_db(tid)
        flash(f"Shop '{biz}' created successfully.", "success")
        return redirect(url_for("admin.index"))
    return render_template("admin/new_tenant.html")


@admin_bp.route("/tenants/<int:tid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_tenant(tid):
    tenant = get_user_by_id(tid)
    if not tenant:
        flash("Tenant not found.", "danger")
        return redirect(url_for("admin.index"))
    if request.method == "POST":
        kwargs = {
            "business_name": request.form.get("business_name", "").strip(),
            "email":         request.form.get("email", "").strip(),
            "is_active":     1 if request.form.get("is_active") else 0,
        }
        pwd = request.form.get("password", "").strip()
        if pwd:
            kwargs["password"] = pwd
        update_tenant(tid, **kwargs)
        flash("Tenant updated.", "success")
        return redirect(url_for("admin.index"))
    return render_template("admin/edit_tenant.html", tenant=tenant)


@admin_bp.route("/tenants/<int:tid>/delete", methods=["POST"])
@login_required
@admin_required
def remove_tenant(tid):
    import shutil
    from .tenant_db import _db_path
    db_file = _db_path(tid)
    if os.path.exists(db_file):
        os.remove(db_file)
    delete_tenant(tid)
    flash("Tenant deleted.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/impersonate/<int:tid>")
@login_required
@admin_required
def impersonate(tid):
    tenant = get_user_by_id(tid)
    if not tenant:
        flash("Tenant not found.", "danger")
        return redirect(url_for("admin.index"))
    session["impersonate_id"] = tid
    flash(f"Now viewing as: {tenant['business_name']}", "info")
    return redirect(url_for("dashboard.index"))


@admin_bp.route("/api/tenants")
@login_required
@admin_required
def api_tenants():
    from flask import jsonify
    tenants = get_all_tenants()
    return jsonify([{
        "id":            t["id"],
        "business_name": t["business_name"],
        "username":      t["username"],
        "email":         t["email"] or "",
        "is_active":     bool(t["is_active"]),
    } for t in tenants])


@admin_bp.route("/stop-impersonate")
@login_required
def stop_impersonate():
    session.pop("impersonate_id", None)
    flash("Returned to admin view.", "info")
    return redirect(url_for("admin.index"))
