from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from ..tenant_db import (get_deleted_products, restore_product,
                          permanent_delete_product, permanent_delete_all_deleted,
                          get_conn)

recycle_bp = Blueprint("recycle", __name__)


@recycle_bp.route("/")
@login_required
def index():
    items = get_deleted_products(current_user.tenant_id)
    return render_template("recycle_bin.html", items=items)


@recycle_bp.route("/<int:pid>/restore", methods=["POST"])
@login_required
def restore(pid):
    restore_product(current_user.tenant_id, pid)
    return jsonify(ok=True)


@recycle_bp.route("/<int:pid>/delete", methods=["POST"])
@login_required
def permanent_delete(pid):
    permanent_delete_product(current_user.tenant_id, pid)
    return jsonify(ok=True)


@recycle_bp.route("/restore-all", methods=["POST"])
@login_required
def restore_all():
    tid = current_user.tenant_id
    conn = get_conn(tid)
    conn.execute("UPDATE products SET deleted=0, deleted_at=NULL WHERE deleted=1")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@recycle_bp.route("/empty", methods=["POST"])
@login_required
def empty():
    permanent_delete_all_deleted(current_user.tenant_id)
    return jsonify(ok=True)
