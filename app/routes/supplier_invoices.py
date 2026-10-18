"""Supplier invoice upload and processing routes."""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
import os
import sqlite3
from pathlib import Path
from datetime import datetime
import mimetypes
import secrets

from app.tenant_db import get_conn

supplier_invoices_bp = Blueprint("supplier_invoices", __name__, url_prefix="/suppliers/invoices")

# File upload configuration
ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_invoice_storage_dir(tenant_id):
    """Get persistent storage directory for invoices, fail safely without it."""
    configured = os.environ.get("AUTOSTACK_INVOICE_DIR")
    production = bool(os.environ.get("RENDER")) or os.environ.get(
        "AUTOSTACK_ENV", "production").lower() not in {"development", "test"}
    if not production and (current_app.testing or current_app.debug):
        root = Path(configured or Path(os.environ["AUTOSTACK_DATA_DIR"]) / "invoices")
    else:
        # Writability is not evidence of durability. Require an actual mount.
        mount_value = os.environ.get("AUTOSTACK_INVOICE_MOUNT")
        if not configured or not mount_value:
            raise RuntimeError("Persistent invoice storage is not configured")
        root, mount = Path(configured), Path(mount_value)
        if not root.is_absolute() or not mount.is_absolute():
            raise RuntimeError("Invoice storage paths must be absolute")
        mount = mount.resolve()
        if mount == Path(mount.anchor) or not os.path.ismount(mount):
            raise RuntimeError("Invoice persistent volume is not mounted")
        if not root.resolve().is_relative_to(mount):
            raise RuntimeError("Invoice directory is outside the persistent volume")
    return str(root.resolve() / str(int(tenant_id)))


def _verify_storage_location(storage_dir):
    """Check writability only after persistence configuration has been verified."""
    import tempfile
    try:
        Path(storage_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=storage_dir) as probe:
            probe.write(b"test")
        return True
    except OSError:
        return False


def _generate_safe_filename(original_filename, file_ext):
    """Generate safe server-side filename without modifying extension."""
    # Use original filename only for display, generate safe server name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(4)
    safe_name = f"invoice_{timestamp}_{random_suffix}{file_ext.lower()}"
    return safe_name


def _validate_file(file_obj):
    """Validate uploaded file."""
    if not file_obj or file_obj.filename == "":
        return False, "No file selected"

    # Check file extension
    _, ext = os.path.splitext(file_obj.filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"

    # Check file size
    file_obj.seek(0, 2)  # Seek to end
    size = file_obj.tell()
    file_obj.seek(0)  # Reset to start

    if size > MAX_FILE_SIZE:
        return False, f"File too large. Maximum size: {MAX_FILE_SIZE / 1024 / 1024:.1f} MB"

    if size == 0:
        return False, "File is empty"

    return True, None


def _verify_mime_type(file_obj, file_ext):
    """Verify MIME type matches extension."""
    file_obj.seek(0)
    header = file_obj.read(8)
    file_obj.seek(0)

    # Check magic bytes
    if file_ext.lower() == '.pdf':
        if not header.startswith(b'%PDF'):
            return False, "File is not a valid PDF"
    elif file_ext.lower() in {'.jpg', '.jpeg'}:
        if not (header[:2] == b'\xff\xd8'):
            return False, "File is not a valid JPEG"
    elif file_ext.lower() == '.png':
        if header[:4] != b'\x89PNG':
            return False, "File is not a valid PNG"

    return True, None


@supplier_invoices_bp.route("/upload", methods=["POST"])
@login_required
def upload_invoice():
    """Upload supplier invoice file."""
    tenant_id = current_user.tenant_id

    # Verify storage is configured
    try:
        storage_dir = _get_invoice_storage_dir(tenant_id)
    except (RuntimeError, OSError, ValueError, KeyError):
        storage_dir = None
    if not storage_dir or not _verify_storage_location(storage_dir):
        return jsonify({
            "success": False,
            "error": "Invoice storage not available. Contact administrator."
        }), 503

    # Get uploaded file
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    file_obj = request.files["file"]

    # Validate file
    is_valid, error = _validate_file(file_obj)
    if not is_valid:
        return jsonify({"success": False, "error": error}), 400

    # Verify MIME type
    _, file_ext = os.path.splitext(file_obj.filename)
    is_valid_mime, mime_error = _verify_mime_type(file_obj, file_ext)
    if not is_valid_mime:
        return jsonify({"success": False, "error": mime_error}), 400

    # Generate safe filename
    safe_filename = _generate_safe_filename(file_obj.filename, file_ext)
    file_path = os.path.join(storage_dir, safe_filename)

    try:
        # Save file without modification
        file_obj.seek(0)
        with open(file_path, "wb") as f:
            f.write(file_obj.read())

        # Get file size and MIME type
        file_size = os.path.getsize(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)

        # Get invoice metadata from form
        supplier_name = request.form.get("supplier_name", "").strip()
        invoice_number = request.form.get("invoice_number", "").strip()
        invoice_date = request.form.get("invoice_date", "")

        if not supplier_name or not invoice_number:
            os.remove(file_path)
            return jsonify({
                "success": False,
                "error": "Supplier name and invoice number required"
            }), 400

        # Store in database
        conn = get_conn(tenant_id)
        try:
            # Check for duplicate (same supplier + invoice number)
            existing = conn.execute(
                "SELECT id FROM supplier_invoices WHERE lower(trim(supplier_name)) = lower(trim(?)) AND lower(trim(invoice_number)) = lower(trim(?))",
                (supplier_name, invoice_number)
            ).fetchone()

            if existing:
                conn.close()
                os.remove(file_path)
                return jsonify({
                    "success": False,
                    "error": f"Invoice {invoice_number} from {supplier_name} already uploaded"
                }), 409

            # Insert invoice record
            cursor = conn.execute(
                """INSERT INTO supplier_invoices
                   (supplier_name, invoice_number, invoice_date, file_name, file_path,
                    file_size, mime_type, status, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'uploaded', ?)""",
                (supplier_name, invoice_number, invoice_date or None,
                 file_obj.filename, file_path, file_size, mime_type, current_user.username)
            )

            invoice_id = cursor.lastrowid
            conn.commit()
            conn.close()

            return jsonify({
                "success": True,
                "message": "Invoice uploaded successfully",
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "supplier_name": supplier_name,
                "status": "uploaded"
            }), 201

        except sqlite3.IntegrityError as e:
            conn.close()
            os.remove(file_path)
            return jsonify({
                "success": False,
                "error": "Database error: duplicate invoice"
            }), 409
        except Exception as e:
            conn.close()
            os.remove(file_path)
            current_app.logger.error(f"Upload error: {e}")
            return jsonify({
                "success": False,
                "error": "Failed to save invoice"
            }), 500

    except IOError as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        current_app.logger.error(f"File write error: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to write file to disk"
        }), 500
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        current_app.logger.error(f"Upload error: {e}")
        return jsonify({
            "success": False,
            "error": "Upload failed"
        }), 500


@supplier_invoices_bp.route("/<int:invoice_id>", methods=["GET"])
@login_required
def get_invoice(invoice_id):
    """Get invoice metadata."""
    tenant_id = current_user.tenant_id

    conn = get_conn(tenant_id)
    invoice = conn.execute(
        "SELECT id, supplier_name, invoice_number, invoice_date, upload_date, status FROM supplier_invoices WHERE id = ?",
        (invoice_id,)
    ).fetchone()
    conn.close()

    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404

    return jsonify({
        "id": invoice["id"],
        "supplier_name": invoice["supplier_name"],
        "invoice_number": invoice["invoice_number"],
        "invoice_date": invoice["invoice_date"],
        "upload_date": invoice["upload_date"],
        "status": invoice["status"]
    })


@supplier_invoices_bp.route("/<int:invoice_id>/extract", methods=["POST"])
@login_required
def extract_invoice(invoice_id):
    """Extract only; confirmation and inventory writes are deliberately separate."""
    from app.services.supplier_invoice_extraction import extract_invoice as extract, ExtractionError
    try:
        result = extract(current_user.tenant_id, invoice_id, current_user.username)
        from flask import url_for
        result["review_url"] = url_for("supplier_invoices.review_page", invoice_id=invoice_id)
        return jsonify(result)
    except ExtractionError as exc:
        return jsonify({"success": False, "error": str(exc)}), exc.status_code


@supplier_invoices_bp.route("/<int:invoice_id>/extraction", methods=["GET"])
@login_required
def get_extraction(invoice_id):
    import json
    conn = get_conn(current_user.tenant_id)
    try:
        row = conn.execute("SELECT status, extracted_json, extraction_error FROM supplier_invoices WHERE id=?",
                           (invoice_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Invoice not found"}), 404
        return jsonify({"status": row["status"], "extraction": json.loads(row["extracted_json"]),
                        "error": row["extraction_error"]})
    finally:
        conn.close()


@supplier_invoices_bp.route("/", methods=["GET"])
@login_required
def review_index():
    from flask import render_template, url_for
    from app.services.supplier_invoice_history import history
    from app.services.supplier_invoice_review import ReviewError
    try:
        state = history(current_user.tenant_id, request.args)
    except ReviewError as exc:
        return render_template("supplier_invoice_list.html", error=str(exc)), exc.status_code
    def page_url(page):
        values = {key:value for key,value in state["filters"].items() if value and key != "page"}
        return url_for("supplier_invoices.review_index", **values, page=page)
    return render_template("supplier_invoice_list.html", **state, page_url=page_url)



@supplier_invoices_bp.route("/<int:invoice_id>/review", methods=["GET"])
@login_required
def review_page(invoice_id):
    from flask import render_template, abort
    from app.services.supplier_invoice_review import load_review, ReviewError
    try:
        state = load_review(current_user.tenant_id, invoice_id)
    except ReviewError as exc:
        abort(exc.status_code, description=str(exc))
    conn = get_conn(current_user.tenant_id)
    try:
        mime = conn.execute("SELECT mime_type FROM supplier_invoices WHERE id=?", (invoice_id,)).fetchone()[0]
    finally:
        conn.close()
    return render_template("supplier_invoice_review.html", state=state, mime_type=mime)


@supplier_invoices_bp.route("/<int:invoice_id>/review-data", methods=["GET"])
@login_required
def review_data(invoice_id):
    from app.services.supplier_invoice_review import load_review, ReviewError
    try:
        return jsonify(load_review(current_user.tenant_id, invoice_id))
    except ReviewError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@supplier_invoices_bp.route("/<int:invoice_id>/review", methods=["POST"])
@login_required
def save_review_draft(invoice_id):
    from app.services.supplier_invoice_review import save_review, ReviewError
    if not request.is_json:
        return jsonify({"error": "Submit a JSON review draft"}), 400
    try:
        return jsonify(save_review(current_user.tenant_id, invoice_id, request.get_json(), current_user.username))
    except ReviewError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@supplier_invoices_bp.route("/products", methods=["GET"])
@login_required
def search_review_products():
    from app.services.supplier_invoice_review import _products, matches
    allowed = {"q", "code", "tyre_size", "brand", "pattern_model"}
    if any(key not in allowed or len(request.args.getlist(key)) != 1 for key in request.args):
        return jsonify({"error": "Invalid product search"}), 400
    args = {key: request.args.get(key, "").strip() for key in allowed}
    if any(len(value) > 120 for value in args.values()):
        return jsonify({"error": "Search is too long"}), 400
    conn = get_conn(current_user.tenant_id)
    try:
        return jsonify({"products": matches(_products(conn), query=args.pop("q"), **args)})
    finally:
        conn.close()


@supplier_invoices_bp.route("/<int:invoice_id>/original", methods=["GET"])
@login_required
def original_invoice(invoice_id):
    return _serve_original(invoice_id, False)


def _serve_original(invoice_id, download):
    from io import BytesIO
    from flask import send_file, render_template
    from app.services.supplier_invoice_history import original_document
    from app.services.supplier_invoice_review import ReviewError
    try:
        content, mime, ext = original_document(current_user.tenant_id, invoice_id)
        response = send_file(BytesIO(content), mimetype=mime,
            download_name=f"supplier-invoice-{invoice_id}" + ext,
            as_attachment=download, max_age=0, conditional=False, etag=False)
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'; sandbox"
        return response
    except ReviewError as exc:
        return render_template("supplier_invoice_file_error.html", error=str(exc), invoice_id=invoice_id), exc.status_code


@supplier_invoices_bp.route("/<int:invoice_id>/download", methods=["GET"])
@login_required
def download_original(invoice_id):
    return _serve_original(invoice_id, True)


@supplier_invoices_bp.route("/<int:invoice_id>/details", methods=["GET"])
@login_required
def invoice_details_page(invoice_id):
    from flask import render_template, abort
    from app.services.supplier_invoice_history import invoice_details, display_amount
    from app.services.supplier_invoice_review import ReviewError
    try:
        state = invoice_details(current_user.tenant_id, invoice_id)
        return render_template("supplier_invoice_details.html", **state, amount=display_amount)
    except ReviewError as exc:
        abort(exc.status_code, description=str(exc))


@supplier_invoices_bp.route("/new", methods=["GET"])
@login_required
def new_invoice_page():
    from flask import render_template
    return render_template("supplier_invoice_new.html")



@supplier_invoices_bp.route("/<int:invoice_id>/confirm", methods=["GET", "POST"])
@login_required
def confirm_supplier_invoice(invoice_id):
    from flask import render_template, url_for
    from app.services.supplier_invoice_confirmation import confirmation_preview, confirm_invoice
    from app.services.supplier_invoice_review import ReviewError
    try:
        if request.method == "GET":
            state = confirmation_preview(current_user.tenant_id, invoice_id)
            return render_template("supplier_invoice_confirmation.html", state=state)
        if not request.is_json:
            raise ReviewError("Submit a JSON confirmation")
        result = confirm_invoice(current_user.tenant_id, invoice_id, request.get_json(), current_user.username)
        result["receipt_url"] = url_for("supplier_invoices.processed_invoice", invoice_id=invoice_id)
        result["details_url"] = url_for("supplier_invoices.invoice_details_page", invoice_id=invoice_id)
        return jsonify(result)
    except ReviewError as exc:
        if request.method == "GET":
            return render_template("supplier_invoice_confirmation.html", error=str(exc), invoice_id=invoice_id), exc.status_code
        return jsonify({"success":False,"error":str(exc)}), exc.status_code
    except Exception:
        current_app.logger.exception("Supplier invoice confirmation failed")
        return jsonify({"success":False,"error":"Confirmation could not complete. No success is recorded. Reload the invoice before retrying."}), 500


@supplier_invoices_bp.route("/<int:invoice_id>/processed", methods=["GET"])
@login_required
def processed_invoice(invoice_id):
    from flask import render_template, abort
    from app.services.supplier_invoice_confirmation import processed_receipt
    from app.services.supplier_invoice_review import ReviewError
    try:
        return render_template("supplier_invoice_processed.html", receipt=processed_receipt(current_user.tenant_id, invoice_id))
    except ReviewError as exc:
        abort(exc.status_code, description=str(exc))
