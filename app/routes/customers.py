"""Customer Management routes for Phase B.

Features:
- Add customer (with optional fields)
- View customer details
- Edit customer
- Search customers by name, phone, vehicle registration
- Link to debtor balance when available
- Walk-in customer support (NULL customer_id)
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import date
from ..tenant_db import (
    add_customer, get_customer, get_all_customers, update_customer, search_customers,
    get_debtor_by_customer_id, get_debtors_by_customer_id, customer_total_outstanding,
    outstanding_balance, list_invoices_by_customer,
)

customers_bp = Blueprint("customers", __name__)


@customers_bp.route("/")
@login_required
def index():
    """List all customers with optional search."""
    tid = current_user.tenant_id
    query = request.args.get("q", "").strip()

    if query:
        customers = search_customers(tid, query)
    else:
        customers = get_all_customers(tid)

    # Enrich each customer with total outstanding balance (across all debtors)
    for c in customers:
        total_outstanding = customer_total_outstanding(tid, c["id"])
        if total_outstanding > 0:
            c["outstanding_balance"] = total_outstanding
        else:
            c["outstanding_balance"] = None

    return render_template(
        "customers.html",
        customers=customers,
        search_query=query,
        total_count=len(customers)
    )


@customers_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    """Add a new customer."""
    tid = current_user.tenant_id

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        vehicle_registration = request.form.get("vehicle_registration", "").strip()
        notes = request.form.get("notes", "").strip()

        # Validate required field
        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customer_form.html", mode="add")

        try:
            customer_id = add_customer(
                tid,
                name=name,
                phone=phone,
                email=email,
                vehicle_registration=vehicle_registration,
                notes=notes
            )
            flash(f"Customer '{name}' added successfully.", "success")
            return redirect(url_for("customers.detail", cid=customer_id))
        except Exception as e:
            flash(f"Error adding customer: {str(e)}", "danger")
            return render_template("customer_form.html", mode="add")

    return render_template("customer_form.html", mode="add")


@customers_bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit(cid):
    """Edit an existing customer."""
    tid = current_user.tenant_id
    customer = get_customer(tid, cid)

    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        vehicle_registration = request.form.get("vehicle_registration", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customer_form.html", mode="edit", customer=customer)

        try:
            update_customer(
                tid,
                cid,
                name=name,
                phone=phone,
                email=email,
                vehicle_registration=vehicle_registration,
                notes=notes
            )
            flash(f"Customer '{name}' updated successfully.", "success")
            return redirect(url_for("customers.detail", cid=cid))
        except Exception as e:
            flash(f"Error updating customer: {str(e)}", "danger")
            return render_template("customer_form.html", mode="edit", customer=customer)

    return render_template("customer_form.html", mode="edit", customer=customer)


@customers_bp.route("/<int:cid>")
@login_required
def detail(cid):
    """View customer details with linked debtor info and transaction history."""
    tid = current_user.tenant_id
    customer = get_customer(tid, cid)

    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.index"))

    # Get all open debtors for this customer (Phase C.0)
    debtors = get_debtors_by_customer_id(tid, cid)
    outstanding = customer_total_outstanding(tid, cid)

    # Get transaction history (invoices)
    invoices = list_invoices_by_customer(tid, cid)

    return render_template(
        "customer_detail.html",
        customer=customer,
        debtors=debtors,
        outstanding_balance=outstanding if outstanding > 0 else None,
        invoices=invoices,
        today=date.today().isoformat()
    )


@customers_bp.route("/search")
@login_required
def search_api():
    """JSON API for customer search (used by POS and other features).

    Query parameters:
        q: Search query (name, phone, or vehicle registration)

    Returns:
        JSON array with minimal customer information:
        - id
        - name
        - phone
        - vehicle_registration
    """
    tid = current_user.tenant_id
    query = request.args.get("q", "").strip()

    if not query or len(query) < 2:
        # Require at least 2 characters to avoid too many results
        return jsonify([])

    results = search_customers(tid, query)

    # Return minimal information
    return jsonify([
        {
            "id": c["id"],
            "name": c["name"],
            "phone": c["phone"],
            "vehicle_registration": c["vehicle_registration"]
        }
        for c in results
    ])
