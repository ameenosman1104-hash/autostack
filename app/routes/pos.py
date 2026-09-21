"""Point of Sale (POS) routes for tyre shop cashier interface."""

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
import uuid

from ..tenant_db import (
    get_all_products, get_all_services, search_customers,
    complete_sale, generate_invoice_number
)

pos_bp = Blueprint("pos", __name__)


@pos_bp.route("/")
@login_required
def index():
    """POS main page."""
    tid = current_user.tenant_id
    return render_template("pos.html", tenant_id=tid)


@pos_bp.route("/products/search")
@login_required
def search_products():
    """Search products by query (tyre size, brand, code, name)."""
    tid = current_user.tenant_id
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify([])

    products = get_all_products(tid, query)

    # Filter to only product data needed for POS
    results = []
    for p in products:
        results.append({
            "id": p["id"],
            "code": p["code"],
            "name": p["name"],
            "brand": p.get("brand", ""),
            "tyre_size": p.get("tyre_size", ""),
            "condition": p.get("condition", ""),
            "current_stock": p["current_stock"],
            "selling_price": p.get("selling_price")  # Column (authoritative)
        })

    return jsonify(results)


@pos_bp.route("/services")
@login_required
def get_services():
    """Get active services for POS."""
    tid = current_user.tenant_id
    services = get_all_services(tid, active_only=True)

    results = [
        {
            "id": s["id"],
            "name": s["name"],
            "default_price": s["default_price"]
        }
        for s in services
    ]

    return jsonify(results)


@pos_bp.route("/customers/search")
@login_required
def search_cust():
    """Search customers by name, phone, vehicle registration."""
    tid = current_user.tenant_id
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify([])

    customers = search_customers(tid, query)

    results = [
        {
            "id": c["id"],
            "name": c["name"],
            "phone": c.get("phone", ""),
            "vehicle_registration": c.get("vehicle_registration", "")
        }
        for c in customers
    ]

    return jsonify(results)


@pos_bp.route("/complete", methods=["POST"])
@login_required
def complete_sale_route():
    """Complete a sale using Phase C transaction engine."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    try:
        # Validate required fields
        if not data.get("idempotency_key"):
            return jsonify({"success": False, "error": "idempotency_key required"}), 400

        if not data.get("items"):
            return jsonify({"success": False, "error": "items required"}), 400

        # Generate invoice number if not provided
        invoice_number = data.get("invoice_number") or generate_invoice_number(tid)

        # Call Phase C transaction engine
        result = complete_sale(
            tid=tid,
            invoice_number=invoice_number,
            idempotency_key=data["idempotency_key"],
            items=data["items"],
            customer_id=data.get("customer_id"),
            payment_method=data.get("payment_method", "cash"),
            discount=float(data.get("discount", 0)),
            notes=data.get("notes", ""),
            created_by=current_user.id
        )

        return jsonify(result), 200

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "error_type": "validation"
        }), 400
    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Database error processing sale",
            "error_type": "system"
        }), 500
