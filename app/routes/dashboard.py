import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from ..tenant_db import get_stats, get_low_stock_products, init_tenant_db, get_setting, save_setting

dashboard_bp = Blueprint("dashboard", __name__)

DEFAULT_SLIDES = [
    {
        "url": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=1200&h=380&fit=crop&q=80",
        "title": "Dunlop Zone",
        "subtitle": "Premium Tyre Solutions — Performance You Can Feel"
    },
    {
        "url": "https://images.unsplash.com/photo-1580273916550-e323be2ae537?w=1200&h=380&fit=crop&q=80",
        "title": "Dunlop Zone",
        "subtitle": "Quality & Safety on Every Road"
    },
    {
        "url": "https://images.unsplash.com/photo-1494976388531-d1058494cdd8?w=1200&h=380&fit=crop&q=80",
        "title": "Dunlop Zone",
        "subtitle": "Expert Fitting — Trusted by Drivers"
    },
]


def _get_slides(tid):
    raw = get_setting(tid, "carousel_slides", "")
    if raw:
        try:
            slides = json.loads(raw)
            if slides:
                return slides
        except Exception:
            pass
    return DEFAULT_SLIDES


@dashboard_bp.route("/")
@login_required
def index():
    tid = current_user.tenant_id
    init_tenant_db(tid)
    stats  = get_stats(tid)
    low    = get_low_stock_products(tid)
    slides = _get_slides(tid)
    return render_template("dashboard.html", stats=stats, low=low, slides=slides)


@dashboard_bp.route("/carousel/save", methods=["POST"])
@login_required
def save_carousel():
    tid = current_user.tenant_id
    slides = []
    for i in range(1, 6):  # support up to 5 slides
        url   = request.form.get(f"slide_url_{i}", "").strip()
        title = request.form.get(f"slide_title_{i}", "").strip()
        sub   = request.form.get(f"slide_sub_{i}", "").strip()
        if url:
            slides.append({"url": url, "title": title, "subtitle": sub})
    if not slides:
        slides = DEFAULT_SLIDES
    save_setting(tid, "carousel_slides", json.dumps(slides))
    flash("Carousel updated.", "success")
    return redirect(url_for("dashboard.index"))
