"""Services under Stock Management: list, search, create, edit, price and activate or deactivate."""
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from ..services import service_catalogue as cat
from ..services.job_cards import JobCardError

service_catalogue_bp = Blueprint("service_catalogue", __name__)


@service_catalogue_bp.errorhandler(JobCardError)
def _service_error(exc):
    return jsonify({"success": False, "error": str(exc)}), 400


@service_catalogue_bp.route("/inventory/services/")
@login_required
def index():
    query = request.args.get("q", "").strip()
    rows = cat.list_services(current_user.tenant_id, query=query)
    return render_template("services_catalogue.html", rows=rows, query=query)


@service_catalogue_bp.route("/inventory/services/new", methods=["POST"])
@login_required
def create():
    data = request.get_json(silent=True) or {}
    record = cat.create_service(current_user.tenant_id, data.get("name"), data.get("default_price"),
                                data.get("description", ""), data.get("category", ""))
    return jsonify(record), 201


@service_catalogue_bp.route("/inventory/services/<int:service_id>/save", methods=["POST"])
@login_required
def save(service_id):
    data = request.get_json(silent=True) or {}
    record = cat.update_service(current_user.tenant_id, service_id, data.get("name"), data.get("default_price"),
                                data.get("description", ""), data.get("category", ""),
                                is_active=data.get("is_active", True) is True)
    return jsonify(record)
