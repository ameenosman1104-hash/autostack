"""Exactly-once tenant-local receiving. All writes share one SQLite transaction."""
import hashlib
import json
import re
import sqlite3
from decimal import Decimal

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import ValidationError

from app.tenant_db import get_conn
from app.services.supplier_invoice_review import ReviewDraft, ReviewError, _invoice, normalized


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="supplier-invoice-confirmation-v1")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _prepare(conn, invoice_id):
    invoice = _invoice(conn, invoice_id)
    if invoice["status"] not in {"extracted", "reviewed"}:
        raise ReviewError("Invoice is not available for confirmation", 409)
    # The same scan must not be received again under edited supplier/reference text.
    # Confirmation calls this under BEGIN IMMEDIATE, serializing the check and status write.
    if invoice.get("extraction_sha256") and conn.execute(
        "SELECT id FROM supplier_invoices WHERE extraction_sha256=? AND status='processed' AND id!=?",
        (invoice["extraction_sha256"], invoice_id),
    ).fetchone():
        raise ReviewError("This original document has already been processed. No additional stock can be added.", 409)
    row = conn.execute("SELECT * FROM supplier_invoice_reviews WHERE supplier_invoice_id=?", (invoice_id,)).fetchone()
    if not row:
        raise ReviewError("Save a reviewed draft before confirming", 409)
    try:
        draft = ReviewDraft.model_validate_json(row["draft_json"])
    except ValidationError as exc:
        raise ReviewError("Saved draft is invalid. Correct it before confirmation.") from exc
    if not all((draft.supplier_name, draft.invoice_number, draft.invoice_date, draft.items)):
        raise ReviewError("Supplier, invoice number, date and product lines are required")
    if any(value is None for value in (draft.subtotal, draft.vat, draft.invoice_total, draft.line_totals_include_vat)):
        raise ReviewError("Complete subtotal, VAT, invoice total and line VAT basis before confirming")
    if draft.version != row["version"]:
        raise ReviewError("Draft version is inconsistent", 409)
    tolerance = Decimal("0.02")
    if abs(draft.subtotal + draft.vat - draft.invoice_total) > tolerance:
        raise ReviewError("Subtotal plus VAT must equal the invoice total")
    lines, identities, new_codes = [], [], set()
    net_sum = vat_sum = Decimal(0)
    for index, item in enumerate(draft.items):
        if not item.product_description or any(v is None for v in (item.quantity, item.unit_cost, item.line_total, item.vat)):
            raise ReviewError(f"Line {index+1}: description, quantity, unit cost, line total and VAT are required")
        net = item.line_total - item.vat if draft.line_totals_include_vat else item.line_total
        if net < 0 or abs(item.quantity * item.unit_cost - net) > tolerance:
            raise ReviewError(f"Line {index+1}: quantity times unit cost must equal the line total excluding VAT")
        net_sum += net
        vat_sum += item.vat
        product = None
        if item.match_type == "existing" and item.matched_product_id is not None:
            found = conn.execute("SELECT * FROM products WHERE id=? AND COALESCE(deleted,0)=0", (item.matched_product_id,)).fetchone()
            if not found:
                raise ReviewError(f"Line {index+1}: selected product is no longer available", 409)
            product = dict(found)
            # Stock and prices are deliberately excluded: always use their actual transaction-time values.
            identities.append({key: product[key] for key in ("id", "code", "name", "extra_data", "category", "unit", "brand", "tyre_size", "pattern", "condition")})
            current = Decimal(str(product["current_stock"]))
            if not current.is_finite() or current < 0:
                raise ReviewError("Selected product has an invalid stock balance")
        elif item.match_type == "new" and item.matched_product_id is None:
            if not item.product_code or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/ -]{0,99}", item.product_code):
                raise ReviewError(f"Line {index+1}: new product needs a valid code (letters, numbers, spaces, . _ / -)")
            key = item.product_code.casefold()
            if key in new_codes or conn.execute("SELECT id FROM products WHERE lower(trim(code))=lower(trim(?))", (item.product_code,)).fetchone():
                raise ReviewError(f"Line {index+1}: product code already exists; select the existing product", 409)
            new_codes.add(key)
            current = Decimal(0)
            identities.append({"new_code": key})
        else:
            raise ReviewError(f"Line {index+1}: explicitly select a product or approve a new product proposal")
        lines.append({"index": index, "code": product["code"] if product else item.product_code,
                      "name": product["name"] if product else item.product_description,
                      "product_id": product["id"] if product else None, "new": product is None,
                      "quantity": str(item.quantity), "unit_cost": str(item.unit_cost),
                      "line_total": str(item.line_total), "vat": str(item.vat),
                      "current_stock": str(current), "projected_stock": str(current + item.quantity)})
    if abs(net_sum-draft.subtotal)>tolerance or abs(vat_sum-draft.vat)>tolerance:
        raise ReviewError("Line totals and VAT must reconcile with invoice subtotal and VAT")
    grouped = {}
    for line in lines:
        if line["product_id"] is not None:
            pid = line["product_id"]
            grouped[pid] = grouped.get(pid, Decimal(0)) + Decimal(line["quantity"])
    for line in lines:
        if line["product_id"] is not None:
            line["projected_stock"] = str(Decimal(line["current_stock"]) + grouped[line["product_id"]])
    identity = normalized(draft.supplier_name) + ":" + normalized(draft.invoice_number)
    if not all(identity.split(":")):
        raise ReviewError("Supplier and invoice number must contain letters or numbers")
    if conn.execute("SELECT supplier_invoice_id FROM supplier_invoice_confirmations WHERE identity_key=?", (identity,)).fetchone():
        raise ReviewError("This supplier invoice has already been processed", 409)
    for other in conn.execute("SELECT supplier_name,invoice_number FROM supplier_invoices WHERE status='processed' AND id!=?", (invoice_id,)):
        if normalized(other["supplier_name"])+":"+normalized(other["invoice_number"]) == identity:
            raise ReviewError("This supplier invoice has already been processed", 409)
    fingerprint = _digest({"draft": draft.model_dump(mode="json"), "products": identities})
    return draft, lines, identity, fingerprint


def confirmation_preview(tenant_id, invoice_id):
    conn = get_conn(tenant_id)
    try:
        conn.execute("BEGIN")
        draft, lines, identity, fingerprint = _prepare(conn, invoice_id)
        token = _serializer().dumps({"tenant": tenant_id, "invoice": invoice_id, "version": draft.version, "hash": fingerprint})
        return {"invoice_id": invoice_id, "draft": draft.model_dump(mode="json"), "lines": lines, "token": token}
    finally:
        conn.rollback()
        conn.close()


def confirm_invoice(tenant_id, invoice_id, payload, actor):
    if not isinstance(payload, dict) or set(payload) != {"token", "confirmed", "approved_new_lines"}:
        raise ReviewError("Invalid confirmation request")
    if payload["confirmed"] is not True or not isinstance(payload["token"], str):
        raise ReviewError("Explicit confirmation is required")
    approved = payload["approved_new_lines"]
    if not isinstance(approved, list) or any(type(i) is not int or i < 0 for i in approved) or len(set(approved)) != len(approved):
        raise ReviewError("Invalid new product approvals")
    try:
        signed = _serializer().loads(payload["token"], max_age=1800)
    except (BadSignature, SignatureExpired) as exc:
        raise ReviewError("Confirmation expired or invalid. Reload the final preview.", 409) from exc
    if signed.get("tenant") != tenant_id or signed.get("invoice") != invoice_id:
        raise ReviewError("Invalid invoice confirmation", 403)
    conn = get_conn(tenant_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        invoice = _invoice(conn, invoice_id)
        prior = conn.execute("SELECT * FROM supplier_invoice_confirmations WHERE supplier_invoice_id=?", (invoice_id,)).fetchone()
        if prior:
            if prior["snapshot_hash"] != signed.get("hash") or prior["review_version"] != signed.get("version"):
                raise ReviewError("This invoice was processed with a different review", 409)
            receipt = json.loads(prior["receipt_json"])
            if set(approved) != set(receipt["new_product_lines"]):
                raise ReviewError("New product approvals do not match", 409)
            return {**receipt, "already_processed": True}
        draft, lines, identity, fingerprint = _prepare(conn, invoice_id)
        if draft.version != signed.get("version") or fingerprint != signed.get("hash"):
            raise ReviewError("Review or product mappings changed. Reload the final preview.", 409)
        new_indices = [line["index"] for line in lines if line["new"]]
        if set(approved) != set(new_indices):
            raise ReviewError("Explicitly approve every proposed new product")
        receipt = {"success": True, "invoice_id": invoice_id, "invoice_number": draft.invoice_number,
                   "supplier_name": draft.supplier_name, "invoice_date": draft.invoice_date,
                   "subtotal": str(draft.subtotal), "vat": str(draft.vat), "invoice_total": str(draft.invoice_total),
                   "line_totals_include_vat": draft.line_totals_include_vat, "review_version": draft.version,
                   "new_product_lines": new_indices, "products_updated": 0, "lines": [], "actor": actor}
        # Unique invoice PK and normalized supplier identity reserve processing within this transaction.
        conn.execute("""INSERT INTO supplier_invoice_confirmations
            (supplier_invoice_id,identity_key,review_version,snapshot_hash,receipt_json,actor) VALUES (?,?,?,?,?,?)""",
            (invoice_id,identity,draft.version,fingerprint,"{}",actor))
        updated_ids = set()
        for item, line in zip(draft.items, lines):
            pid = line["product_id"]
            if line["new"]:
                pid = conn.execute("""INSERT INTO products
                    (code,name,category,unit,current_stock,last_cost_price,previous_cost_price,supplier,extra_data,condition,brand,tyre_size,pattern)
                    VALUES (?,?,'Tyres','PCS',0,0,0,?,?,'new',?,?,?)""",
                    (item.product_code,item.product_description,draft.supplier_name,
                     json.dumps({"brand":item.brand,"tyre_size":item.tyre_size,"pattern":item.pattern_model}),
                     item.brand or "",item.tyre_size or "",item.pattern_model or "")).lastrowid
            product = conn.execute("SELECT * FROM products WHERE id=? AND COALESCE(deleted,0)=0", (pid,)).fetchone()
            before = Decimal(str(product["current_stock"]))
            after = before + item.quantity
            if not after.is_finite() or after > Decimal("1000000000"):
                raise ReviewError("Projected stock exceeds the supported range")
            conn.execute("""UPDATE products SET current_stock=?,previous_cost_price=last_cost_price,
                last_cost_price=?,updated_at=datetime('now') WHERE id=?""", (float(after),float(item.unit_cost),pid))
            history_id = conn.execute("""INSERT INTO stock_history
                (product_id,product_code,product_name,change_type,qty_before,qty_after,change_by,notes)
                VALUES (?,?,?,'supplier_invoice',?,?,?,?)""",
                (pid,product["code"],product["name"],float(before),float(after),float(item.quantity),
                 f"Supplier invoice #{invoice_id} {draft.invoice_number}; supplier {draft.supplier_name}; line {line['index']+1}; actor {actor}")).lastrowid
            receipt["lines"].append({**line,"product_id":pid,"current_stock":str(before),"projected_stock":str(after),"stock_history_id":history_id})
            updated_ids.add(pid)
        receipt["products_updated"] = len(updated_ids)
        conn.execute("UPDATE supplier_invoices SET status='processed',confirmation_date=datetime('now'),updated_at=datetime('now') WHERE id=?", (invoice_id,))
        conn.execute("UPDATE supplier_invoice_confirmations SET receipt_json=? WHERE supplier_invoice_id=?", (json.dumps(receipt),invoice_id))
        conn.commit()
        return {**receipt,"already_processed":False}
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ReviewError("Invoice or product conflicts with existing data. Nothing was added.",409) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def processed_receipt(tenant_id, invoice_id):
    conn = get_conn(tenant_id)
    try:
        _invoice(conn, invoice_id)
        row = conn.execute("SELECT receipt_json,created_at FROM supplier_invoice_confirmations WHERE supplier_invoice_id=?", (invoice_id,)).fetchone()
        if not row:
            raise ReviewError("No processing receipt is available for this invoice",404)
        return {**json.loads(row["receipt_json"]),"processed_at":row["created_at"]}
    finally:
        conn.close()
