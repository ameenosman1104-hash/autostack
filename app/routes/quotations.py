"""Quotations: create, edit drafts, status workflow, print, PDF and WhatsApp share."""
import logging

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from ..services import quotation_output as output
from ..services import quotations as engine
from ..tenant_db import get_all_settings, get_setting, save_setting

quotations_bp = Blueprint("quotations", __name__)
logger = logging.getLogger(__name__)
SETTING_KEYS = ("shop_phone", "shop_email", "shop_address", "shop_logo_path", "currency_symbol",
                "whatsapp_country_code", "quote_validity_days", "quote_max_discount_pct", "quote_staff_can_convert")


def _actor():
    return current_user.username


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


@quotations_bp.errorhandler(engine.QuotationError)
def _quotation_error(exc):
    return _error(str(exc))


@quotations_bp.errorhandler(output.ShareError)
def _share_error(exc):
    return _error(str(exc))


@quotations_bp.route("/")
@login_required
def index():
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    rows = engine.list_quotations(current_user.tenant_id, status=status or None, search=search or None)
    settings = {k: v for k, v in get_all_settings(current_user.tenant_id).items() if k in SETTING_KEYS}
    return render_template("quotations_list.html", rows=rows, statuses=engine.STATUSES,
                           status=status, search=search, settings=settings, is_admin=current_user.is_admin)


@quotations_bp.route("/new")
@login_required
def new():
    return render_template("quotation_form.html", quotation=None, is_admin=current_user.is_admin)


@quotations_bp.route("/<int:qid>/edit")
@login_required
def edit(qid):
    detail = engine.get_quotation(current_user.tenant_id, qid)
    if not detail:
        return redirect(url_for("quotations.index"))
    if detail["header"]["status"] not in engine.EDITABLE_STATUSES:
        flash("Only draft quotations can be edited.", "warning")
        return redirect(url_for("quotations.view", qid=qid))
    return render_template("quotation_form.html", quotation=detail, is_admin=current_user.is_admin)


@quotations_bp.route("/save", methods=["POST"])
@login_required
def save_new():
    data = request.get_json(silent=True) or {}
    result = engine.create_quotation(current_user.tenant_id, data, _actor(), current_user.is_admin)
    result["url"] = url_for("quotations.view", qid=result["id"])
    return jsonify({"success": True, **result}), 201


@quotations_bp.route("/<int:qid>/save", methods=["POST"])
@login_required
def save_existing(qid):
    data = request.get_json(silent=True) or {}
    result = engine.update_quotation(current_user.tenant_id, qid, data, _actor(), current_user.is_admin)
    return jsonify({"success": True, **result})


@quotations_bp.route("/<int:qid>")
@login_required
def view(qid):
    detail = engine.get_quotation(current_user.tenant_id, qid)
    if not detail:
        return redirect(url_for("quotations.index"))
    shop = output.shop_profile(current_user.tenant_id)
    wa_url, wa_error = None, None
    try:
        wa_url = output.whatsapp_url(detail["header"], shop)
    except output.ShareError as exc:
        wa_error = str(exc)
    invoice_no = engine.invoice_number(current_user.tenant_id, detail["header"]["converted_invoice_id"])
    return render_template("quotation_view.html", q=detail, shop=shop, wa_url=wa_url, wa_error=wa_error, invoice_no=invoice_no,
                           can_convert=_can_convert(),
                           statuses=engine.TRANSITIONS.get(detail["header"]["status"], set()))


@quotations_bp.route("/<int:qid>/print")
@login_required
def print_view(qid):
    detail = engine.get_quotation(current_user.tenant_id, qid)
    if not detail:
        return redirect(url_for("quotations.index"))
    shop = output.shop_profile(current_user.tenant_id)
    shop["logo_path"] = shop["logo_path"] if output.logo_file(shop["logo_path"]) else ""
    return render_template("quotation_print.html", q=detail, shop=shop)


@quotations_bp.route("/<int:qid>/pdf")
@login_required
def pdf(qid):
    detail = engine.get_quotation(current_user.tenant_id, qid)
    if not detail:
        return redirect(url_for("quotations.index"))
    shop = output.shop_profile(current_user.tenant_id)
    content = output.render_pdf(detail["header"], detail["items"], shop)
    filename = f"{detail['header']['quotation_number']}.pdf"
    return Response(content, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@quotations_bp.route("/<int:qid>/status", methods=["POST"])
@login_required
def change_status(qid):
    data = request.get_json(silent=True) or {}
    result = engine.set_status(current_user.tenant_id, qid, data.get("status", ""), _actor())
    return jsonify({"success": True, **result})


def _can_convert():
    """Admins always; staff only when an admin has enabled it. Uses the existing is_admin flag and settings."""
    if current_user.is_admin:
        return True
    return get_setting(current_user.tenant_id, "quote_staff_can_convert", "0") == "1"


@quotations_bp.route("/<int:qid>/convert", methods=["POST"])
@login_required
def convert(qid):
    """Create a POS sale from an accepted quotation. Same tenant, same engine, same idempotency key on retry."""
    if not _can_convert():
        return _error("You are not authorised to convert quotations to sales", 403)
    data = request.get_json(silent=True) or {}
    result = engine.convert_to_sale(current_user.tenant_id, qid, _actor(), data.get("payment_method", "cash"))
    result["url"] = url_for("quotations.view", qid=qid)
    return jsonify(result), (200 if result.get("duplicate") else 201)


@quotations_bp.route("/api/customers")
@login_required
def api_customers():
    return jsonify(engine.search_customers(current_user.tenant_id, request.args.get("q", "").strip()))


@quotations_bp.route("/api/customers", methods=["POST"])
@login_required
def api_create_customer():
    data = request.get_json(silent=True) or {}
    return jsonify(engine.create_customer(current_user.tenant_id, data.get("name"), data.get("phone", ""),
                                          data.get("email", ""))), 201


@quotations_bp.route("/api/products")
@login_required
def api_products():
    term = request.args.get("q", "").strip()
    if not term:
        return jsonify([])
    rows = engine.search_products(current_user.tenant_id, term)
    if not current_user.is_admin:
        for row in rows:
            row.pop("current_stock", None)
    return jsonify(rows)


@quotations_bp.route("/api/services")
@login_required
def api_services():
    return jsonify(engine.search_services(current_user.tenant_id))


@quotations_bp.route("/settings", methods=["POST"])
@login_required
def update_settings():
    if not current_user.is_admin:
        return _error("Only administrators can change quotation settings", 403)
    data = request.get_json(silent=True) or {}
    for key in SETTING_KEYS:
        if key in data:
            save_setting(current_user.tenant_id, key, str(data[key] or "").strip()[:500])
    return jsonify({"success": True})
