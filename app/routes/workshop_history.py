"""Vehicle workshop history: search by registration and each vehicle's job history. Read-only."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..services import job_cards as jc
from ..services import workshop_history as wh

workshop_bp = Blueprint("workshop_history", __name__)


@workshop_bp.route("/")
@login_required
def search():
    term = request.args.get("q", "").strip()
    order = request.args.get("order", "newest")
    results = []
    if term:
        try:
            results = wh.search_vehicles(current_user.tenant_id, term)
        except jc.JobCardError as exc:
            flash(str(exc), "warning")
    return render_template("vehicles_search.html", term=term, results=results, order=order, searched=bool(term))


@workshop_bp.route("/<int:vid>")
@login_required
def history(vid):
    order = request.args.get("order", "newest")
    try:
        data = wh.vehicle_history(current_user.tenant_id, vid, order)
    except jc.JobCardError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("workshop_history.search"))
    if data is None:
        flash("Vehicle not found.", "warning")
        return redirect(url_for("workshop_history.search"))
    return render_template("vehicle_history.html", data=data)
