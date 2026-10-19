"""Job cards UI: dashboard, create/edit, vehicles, status, technician and audit history.

Phase 2 scope: no stock deduction and no invoice conversion. Those arrive in Phase 4.
"""
import logging

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..services import job_card_output as jco
from ..services import job_cards as jc
from ..services import job_conversion as jconv
from ..services import quotation_output as qo
from ..services import quotations as engine
from ..tenant_db import get_setting

job_cards_bp = Blueprint("job_cards", __name__)
logger = logging.getLogger(__name__)
CANCEL_SETTING = "job_staff_can_cancel"
CONVERT_SETTING = "job_staff_can_convert"


def _actor():
    return current_user.username


def _can_convert():
    """Admins convert to invoices by default. Staff only when an admin has enabled it in settings."""
    if current_user.is_admin:
        return True
    return get_setting(current_user.tenant_id, CONVERT_SETTING, "0") == "1"


def _can_cancel():
    """Admins may cancel. Staff only when an admin has enabled it in settings."""
    if current_user.is_admin:
        return True
    return get_setting(current_user.tenant_id, CANCEL_SETTING, "0") == "1"


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


@job_cards_bp.errorhandler(jc.JobCardError)
def _job_error(exc):
    return _error(str(exc))


@job_cards_bp.route("/")
@login_required
def index():
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    rows = jc.list_jobs(current_user.tenant_id, status=status or None, search=search or None)
    return render_template("job_cards_list.html", rows=rows, statuses=jc.STATUSES,
                           status=status, search=search, can_cancel=_can_cancel())


@job_cards_bp.route("/new")
@login_required
def new():
    prefill = {}
    if request.args.get("customer_id", "").isdigit():
        prefill["customer_id"] = int(request.args["customer_id"])
    return render_template("job_card_form.html", job=None, prefill=prefill, is_admin=current_user.is_admin)


@job_cards_bp.route("/<int:jid>/edit")
@login_required
def edit(jid):
    detail = jc.get_job(current_user.tenant_id, jid)
    if not detail:
        flash("Job card not found.", "warning")
        return redirect(url_for("job_cards.index"))
    if detail["header"]["status"] not in jc.EDITABLE_STATUSES:
        flash("Only open or in-progress job cards can be edited.", "warning")
        return redirect(url_for("job_cards.view", jid=jid))
    return render_template("job_card_form.html", job=detail, prefill={}, is_admin=current_user.is_admin)


@job_cards_bp.route("/save", methods=["POST"])
@login_required
def save_new():
    data = request.get_json(silent=True) or {}
    result = jc.create_job(current_user.tenant_id, data, _actor(), current_user.is_admin)
    result["url"] = url_for("job_cards.view", jid=result["id"])
    return jsonify({"success": True, **result}), 201


@job_cards_bp.route("/<int:jid>/save", methods=["POST"])
@login_required
def save_existing(jid):
    data = request.get_json(silent=True) or {}
    result = jc.update_job(current_user.tenant_id, jid, data, _actor(), current_user.is_admin)
    result["url"] = url_for("job_cards.view", jid=jid)
    return jsonify({"success": True, **result})


@job_cards_bp.route("/<int:jid>")
@login_required
def view(jid):
    detail = jc.get_job(current_user.tenant_id, jid)
    if not detail:
        flash("Job card not found.", "warning")
        return redirect(url_for("job_cards.index"))
    history = jc.vehicle_history(current_user.tenant_id, detail["header"]["vehicle_id"])
    shop = qo.shop_profile(current_user.tenant_id)
    wa_url, wa_error = None, None
    try:
        wa_url = jco.job_whatsapp_url(detail, shop)
    except jco.PhoneError as exc:
        wa_error = str(exc)
    return render_template("job_card_view.html", job=detail, wa_url=wa_url, wa_error=wa_error,
                           statuses=jc.TRANSITIONS.get(detail["header"]["status"], set()),
                           can_cancel=_can_cancel(), can_convert=_can_convert(),
                           is_admin=current_user.is_admin, history=history)


@job_cards_bp.route("/<int:jid>/status", methods=["POST"])
@login_required
def change_status(jid):
    data = request.get_json(silent=True) or {}
    result = jc.set_status(current_user.tenant_id, jid, data.get("status", ""), _actor(),
                           can_cancel=_can_cancel(), mileage_out=data.get("mileage_out"))
    return jsonify({"success": True, **result})


@job_cards_bp.route("/<int:jid>/technician", methods=["POST"])
@login_required
def change_technician(jid):
    data = request.get_json(silent=True) or {}
    result = jc.set_technician(current_user.tenant_id, jid, data.get("technician", ""), _actor())
    return jsonify({"success": True, **result})


@job_cards_bp.route("/api/customers")
@login_required
def api_customers():
    return jsonify(engine.search_customers(current_user.tenant_id, request.args.get("q", "").strip()))


@job_cards_bp.route("/api/customers", methods=["POST"])
@login_required
def api_create_customer():
    data = request.get_json(silent=True) or {}
    return jsonify(engine.create_customer(current_user.tenant_id, data.get("name"), data.get("phone", ""),
                                          data.get("email", ""))), 201


@job_cards_bp.route("/api/vehicles")
@login_required
def api_vehicles():
    customer_id = request.args.get("customer_id")
    if not (customer_id or "").isdigit():
        return jsonify([])
    return jsonify(jc.list_vehicles(current_user.tenant_id, customer_id=int(customer_id)))


@job_cards_bp.route("/api/vehicles", methods=["POST"])
@login_required
def api_save_vehicle():
    data = request.get_json(silent=True) or {}
    vehicle = jc.upsert_vehicle(current_user.tenant_id, data.get("customer_id"), data.get("registration", ""),
                                make=data.get("make", ""), model=data.get("model", ""),
                                year=data.get("year") or None, notes=data.get("notes", ""), actor=_actor())
    return jsonify(vehicle), 201


@job_cards_bp.route("/api/products")
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


@job_cards_bp.route("/api/services")
@login_required
def api_services():
    return jsonify(engine.search_services(current_user.tenant_id))


def _print_shop():
    """Shop details for printing. The logo is used only if it resolves inside app/static."""
    shop = qo.shop_profile(current_user.tenant_id)
    shop["logo_path"] = shop["logo_path"] if qo.logo_file(shop["logo_path"]) else ""
    return shop


@job_cards_bp.route("/<int:jid>/print")
@login_required
def print_view(jid):
    detail = jc.get_job(current_user.tenant_id, jid)
    if not detail:
        abort(404)
    status = detail["header"]["status"]
    return render_template("job_card_print.html", job=detail, shop=_print_shop(),
                           status_label=jco.STATUS_LABELS.get(status, status))


@job_cards_bp.route("/<int:jid>/pdf")
@login_required
def pdf(jid):
    detail = jc.get_job(current_user.tenant_id, jid)
    if not detail:
        abort(404)
    content = jco.render_job_card_pdf(detail, _print_shop())
    filename = f"{detail['header']['job_number']}.pdf"
    return Response(content, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@job_cards_bp.route("/<int:jid>/invoice")
@login_required
def invoice_screen(jid):
    """Conversion screen. Only authorised users see it; everyone else is sent back to the job card."""
    if not _can_convert():
        flash("You are not authorised to convert job cards to invoices.", "warning")
        return redirect(url_for("job_cards.view", jid=jid))
    try:
        data = jconv.preview(current_user.tenant_id, jid, current_user.is_admin, True)
    except jc.JobCardError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("job_cards.view", jid=jid))
    return render_template("job_card_convert.html", data=data, is_admin=current_user.is_admin)


@job_cards_bp.route("/<int:jid>/convert", methods=["POST"])
@login_required
def convert(jid):
    """Invoice a ready or completed job card through the POS engine. Repeat requests return the same invoice."""
    data = request.get_json(silent=True) or {}
    result = jconv.convert_job(
        current_user.tenant_id, jid, _actor(), data.get("payment_method", ""),
        can_convert=_can_convert(), is_admin=current_user.is_admin,
        payment_confirmed=bool(data.get("payment_confirmed")),
        confirm_price_changes=bool(data.get("confirm_price_changes")))
    return jsonify({"success": True, **result}), (200 if result.get("duplicate") else 201)
