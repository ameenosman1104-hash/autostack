"""Daily Cash-Up pages. Admins only: staff cannot open this area, and no setting grants it."""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..services import cashup as cu
from ..services import shop_time

cashup_bp = Blueprint("cashup", __name__, url_prefix="/cashup")
cashup_bp.app_template_filter("rand")(cu.format_rand)


def can_manage_cashup():
    return bool(getattr(current_user, "is_admin", False))


def cashup_permission(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not can_manage_cashup():
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@cashup_bp.app_context_processor
def _navigation():
    """Show the Cash-Up link in the menu only to people who can open it."""
    return {"can_manage_cashup": bool(current_user.is_authenticated and can_manage_cashup())}


def _actor():
    return current_user.username


def _today():
    from ..tenant_db import shop_business_today
    return shop_business_today(current_user.tenant_id)


@cashup_bp.route("/")
@cashup_permission
def dashboard():
    today = _today()
    tid = current_user.tenant_id
    status = cu.cash_up_status(tid, today)
    figures = cu.day_summary(tid, today)
    return render_template("cashup.html", today=today, status=status, figures=figures,
                           movements=cu.movements(tid, today), late=cu.late_transactions(tid),
                           history_count=len(cu.history(tid)), can_manage=True)


@cashup_bp.route("/open", methods=["POST"])
@cashup_permission
def open_day():
    try:
        cu.open_day(current_user.tenant_id, _today(), cu.parse_money(request.form.get("opening_float"), "Opening float"),
                    _actor())
        flash("Cash-up opened for today.", "success")
    except cu.CashUpError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("cashup.dashboard"))


@cashup_bp.route("/movement", methods=["POST"])
@cashup_permission
def add_movement():
    try:
        cu.add_movement(current_user.tenant_id, _today(), request.form.get("kind", ""),
                        cu.parse_money(request.form.get("amount"), "Amount"),
                        request.form.get("reason", ""), _actor())
        flash("Cash movement recorded.", "success")
    except cu.CashUpError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("cashup.dashboard"))


@cashup_bp.route("/late/include", methods=["POST"])
@cashup_permission
def include_late():
    try:
        result = cu.include_late(current_user.tenant_id, _today(), request.form.get("source_type", ""),
                                 request.form.get("source_id", ""), _actor())
        flash(f"Counted in today's cash-up. Original business date {result['business_date']} kept.", "success")
    except (cu.CashUpError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("cashup.dashboard"))


@cashup_bp.route("/close", methods=["POST"])
@cashup_permission
def close_day():
    try:
        if request.form.get("confirm") != "yes":
            raise cu.CashUpError("Review the summary and tick the confirmation before closing")
        result = cu.close_day(current_user.tenant_id, _today(), cu.parse_money(request.form.get("counted_cash"), "Counted cash"),
                              _actor(), notes=request.form.get("notes", "")[:500],
                              variance_explanation=request.form.get("variance_explanation", "")[:500])
        flash(f"Cash-up closed. Variance {cu.format_rand(result['variance_cents'])}.", "success")
    except cu.CashUpError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("cashup.dashboard"))


@cashup_bp.route("/history")
@cashup_permission
def history():
    return render_template("cashup_history.html", rows=cu.history(current_user.tenant_id))


@cashup_bp.route("/<business_date>")
@cashup_permission
def detail(business_date):
    try:
        status = cu.cash_up_status(current_user.tenant_id, business_date)
    except cu.CashUpError:
        abort(404)
    if not status or status["status"] != "closed":
        flash("That cash-up is not closed yet.", "warning")
        return redirect(url_for("cashup.dashboard"))
    return render_template("cashup_detail.html", cash_up=status, figures=cu.day_summary(current_user.tenant_id, business_date),
                           movements=cu.movements(current_user.tenant_id, business_date))


@cashup_bp.route("/<business_date>/print")
@cashup_permission
def print_view(business_date):
    return detail(business_date)
