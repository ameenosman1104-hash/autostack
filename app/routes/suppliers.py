from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..tenant_db import (get_all_suppliers, get_supplier, add_supplier,
                          update_supplier, delete_supplier)

suppliers_bp = Blueprint("suppliers", __name__)


@suppliers_bp.route("/")
@login_required
def index():
    suppliers = get_all_suppliers(current_user.tenant_id)
    return render_template("suppliers.html", suppliers=suppliers)


@suppliers_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Supplier name is required.", "danger")
            return redirect(url_for("suppliers.add"))
        add_supplier(
            current_user.tenant_id,
            name           = name,
            phone          = request.form.get("phone", "").strip(),
            email          = request.form.get("email", "").strip(),
            address        = request.form.get("address", "").strip(),
            lead_time_days = int(request.form.get("lead_time_days", 0) or 0),
            payment_terms  = request.form.get("payment_terms", "").strip(),
            notes          = request.form.get("notes", "").strip(),
        )
        flash(f"Supplier '{name}' added.", "success")
        return redirect(url_for("suppliers.index"))
    return render_template("supplier_form.html", supplier=None, action="Add")


@suppliers_bp.route("/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def edit(sid):
    tid = current_user.tenant_id
    supplier = get_supplier(tid, sid)
    if not supplier:
        flash("Supplier not found.", "danger")
        return redirect(url_for("suppliers.index"))
    if request.method == "POST":
        update_supplier(tid, sid,
            name           = request.form.get("name", "").strip(),
            phone          = request.form.get("phone", "").strip(),
            email          = request.form.get("email", "").strip(),
            address        = request.form.get("address", "").strip(),
            lead_time_days = int(request.form.get("lead_time_days", 0) or 0),
            payment_terms  = request.form.get("payment_terms", "").strip(),
            notes          = request.form.get("notes", "").strip(),
        )
        flash("Supplier updated.", "success")
        return redirect(url_for("suppliers.index"))
    return render_template("supplier_form.html", supplier=supplier, action="Edit")


@suppliers_bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    delete_supplier(current_user.tenant_id, sid)
    flash("Supplier deleted.", "success")
    return redirect(url_for("suppliers.index"))
