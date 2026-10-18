"""Validated tenant-local invoice drafts and advisory matching. No inventory writes."""
import json
import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator
from app.tenant_db import get_conn
from app.services.supplier_invoice_extraction import InvoiceExtraction, InvoiceLine, review_flags


class ReviewError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class ReviewLine(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    product_code: str | None = Field(default=None, max_length=100)
    product_description: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=100)
    tyre_size: str | None = Field(default=None, max_length=100)
    pattern_model: str | None = Field(default=None, max_length=100)
    quantity: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=3)
    unit_cost: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=4)
    line_total: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=2)
    vat: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=2)
    match_type: Literal["unresolved", "existing", "new"] = "unresolved"
    matched_product_id: StrictInt | None = Field(default=None, gt=0)

    @field_validator("quantity", "unit_cost", "line_total", "vat", mode="before")
    @classmethod
    def numeric_input(cls, value):
        if isinstance(value, bool):
            raise ValueError("A numeric value is required")
        return value

    @field_validator("product_code", "product_description", "brand", "tyre_size", "pattern_model")
    @classmethod
    def strip_text(cls, value):
        if value is None:
            return None
        if any(ord(c) < 32 for c in value):
            raise ValueError("Control characters are not allowed")
        return value.strip() or None


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: StrictInt = Field(ge=0)
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: str | None = None
    subtotal: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=2)
    vat: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=2)
    invoice_total: Decimal | None = Field(default=None, ge=0, le=1000000000, decimal_places=2)
    line_totals_include_vat: StrictBool | None = None
    items: list[ReviewLine] = Field(max_length=200)

    @field_validator("subtotal", "vat", "invoice_total", mode="before")
    @classmethod
    def numeric_input(cls, value):
        return ReviewLine.numeric_input(value)

    @field_validator("supplier_name", "invoice_number")
    @classmethod
    def strip_text(cls, value):
        return ReviewLine.strip_text(value)

    @field_validator("invoice_date")
    @classmethod
    def valid_date(cls, value):
        if value is None or value == "":
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Date must use YYYY-MM-DD")
        date.fromisoformat(value)
        return value


def _invoice(conn, invoice_id):
    row = conn.execute("SELECT * FROM supplier_invoices WHERE id=?", (invoice_id,)).fetchone()
    if row is None:
        raise ReviewError("Invoice not found", 404)
    return dict(row)


def _products(conn):
    return [dict(row) for row in conn.execute("""SELECT id, code, name, current_stock, extra_data,
        category, supplier FROM products WHERE deleted=0 OR deleted IS NULL ORDER BY name""")]


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def matches(products, query="", **hints):
    """Score deterministic hints; a suggestion never becomes an approved mapping."""
    ranked = []
    for product in products:
        text = " ".join(str(product.get(key) or "") for key in ("code", "name", "category", "supplier", "extra_data"))
        searchable = normalized(text)
        if query and not all(normalized(word) in searchable for word in query.split()):
            continue
        reasons, score = [], 0
        for key, weight, label in (("code", 100, "Product code"), ("tyre_size", 40, "Tyre size"),
                                   ("brand", 25, "Brand"), ("pattern_model", 25, "Pattern")):
            value = normalized(hints.get(key))
            is_match = (value == normalized(product["code"]) if key == "code" else value in searchable)
            if value and is_match:
                score += weight
                reasons.append(label)
        if not query and any(hints.values()) and not score:
            continue
        ranked.append({"id": product["id"], "code": product["code"], "name": product["name"],
                       "current_stock": product["current_stock"], "score": score, "reasons": reasons})
    return sorted(ranked, key=lambda item: (-item["score"], item["name"], item["id"]))[:20]


def _initial(invoice):
    source = json.loads(invoice["extracted_json"])
    draft = {key: source.get(key) for key in ("supplier_name", "invoice_number", "invoice_date", "subtotal", "vat", "invoice_total")}
    draft["line_totals_include_vat"] = source.get("line_totals_include_vat")
    draft["version"] = 0
    draft["items"] = [{**{key: item.get(key) for key in ReviewLine.model_fields
                           if key not in {"match_type", "matched_product_id"}},
                       "match_type": "unresolved", "matched_product_id": None} for item in source.get("items", [])]
    return draft


def _context(conn, invoice, draft):
    products = _products(conn)
    by_id = {p["id"]: p for p in products}
    source = json.loads(invoice["extracted_json"])
    notices = []
    # Recalculate validation warnings from edited values; preserve original AI uncertainty separately.
    try:
        validated = InvoiceExtraction.model_validate({
            **{key: draft.get(key) for key in ("supplier_name", "invoice_number", "invoice_date", "subtotal", "vat", "invoice_total")},
            **{key: source.get(key) for key in ("currency", "page_count")},
            "line_totals_include_vat": draft.get("line_totals_include_vat", source.get("line_totals_include_vat")),
            "items": [{**{key: value for key, value in (source.get("items", [])[index] if index < len(source.get("items", [])) else {}).items()
                          if key in {"page", "confidence", "warnings"}},
                       **{key: item.get(key) for key in InvoiceLine.model_fields if key in item}}
                      for index, item in enumerate(draft["items"])]})
        notices = review_flags(validated)
        # The extraction confidence belongs to source evidence, not edited client data.
        notices = [flag for flag in notices if not flag.endswith(".uncertain")]
    except ValidationError:
        notices.append("Some extracted values need correction before saving.")
    duplicates = []
    for row in conn.execute("""SELECT i.id, i.supplier_name, i.invoice_number, r.draft_json FROM supplier_invoices i
        LEFT JOIN supplier_invoice_reviews r ON r.supplier_invoice_id=i.id WHERE i.id != ?""", (invoice["id"],)):
        other = json.loads(row["draft_json"]) if row["draft_json"] else dict(row)
        if (normalized(draft.get("supplier_name")) and normalized(draft.get("invoice_number")) and
            normalized(other.get("supplier_name")) == normalized(draft.get("supplier_name")) and
            normalized(other.get("invoice_number")) == normalized(draft.get("invoice_number"))):
            duplicates.append(row["id"])
    if duplicates:
        notices.append("Possible duplicate supplier invoice. Check the other invoice before confirmation.")
    grouped = {}
    for item in draft["items"]:
        if item.get("match_type") == "existing" and item.get("matched_product_id") in by_id:
            pid = item["matched_product_id"]
            grouped[pid] = grouped.get(pid, Decimal(0)) + Decimal(str(item.get("quantity") or 0))
    previews, estimate, complete = [], Decimal(0), True
    for index, item in enumerate(draft["items"]):
        qty, unit = item.get("quantity"), item.get("unit_cost")
        if qty is None or unit is None:
            complete = False
            cost = None
        else:
            cost = Decimal(str(qty)) * Decimal(str(unit))
            estimate += cost
        selected = by_id.get(item.get("matched_product_id")) if item.get("match_type") == "existing" else None
        current = selected["current_stock"] if selected else (0 if item.get("match_type") == "new" else None)
        projected = None
        if current is not None and qty is not None:
            projected = Decimal(str(current)) + (grouped[selected["id"]] if selected else Decimal(str(qty)))
        suggestions = matches(products, code=item.get("product_code"), tyre_size=item.get("tyre_size"),
                              brand=item.get("brand"), pattern_model=item.get("pattern_model")) if any(
                                  item.get(k) for k in ("product_code", "tyre_size", "brand", "pattern_model")) else []
        if item.get("match_type") == "unresolved":
            notices.append(f"Line {index + 1}: choose an existing product or propose a new product.")
        elif item.get("match_type") == "existing" and selected is None:
            notices.append(f"Line {index + 1}: selected product is no longer available.")
        elif item.get("match_type") == "new":
            if not item.get("product_code") or not item.get("product_description"):
                notices.append(f"Line {index + 1}: a new product proposal needs a code and description.")
            if suggestions:
                notices.append(f"Line {index + 1}: existing products may match this new product proposal.")
        original = source.get("items", [])[index] if index < len(source.get("items", [])) else {}
        previews.append({"current_stock": current, "quantity_to_add": qty,
                         "projected_stock": str(projected) if projected is not None else None,
                         "estimated_cost": str(cost.quantize(Decimal("0.01"))) if cost is not None else None,
                         "selected_product": {k: selected[k] for k in ("id", "code", "name")} if selected else None,
                         "suggestions": suggestions, "uncertain": original.get("confidence", "low") != "high" or bool(original.get("warnings")),
                         "source_warnings": original.get("warnings", [])})
    return {"draft": draft, "previews": previews, "warnings": list(dict.fromkeys(notices)),
            "source_flags": source.get("review_flags", []), "duplicate_ids": duplicates,
            "estimated_purchase_cost": str(estimate.quantize(Decimal("0.01"))), "cost_is_complete": complete,
            "currency": source.get("currency"), "status": invoice["status"], "invoice_id": invoice["id"]}


def load_review(tenant_id, invoice_id):
    conn = get_conn(tenant_id)
    try:
        invoice = _invoice(conn, invoice_id)
        if invoice["status"] not in {"extracted", "reviewed"}:
            raise ReviewError("Extract this invoice before reviewing it. Processed invoices cannot be edited.", 409)
        row = conn.execute("SELECT * FROM supplier_invoice_reviews WHERE supplier_invoice_id=?", (invoice_id,)).fetchone()
        return _context(conn, invoice, json.loads(row["draft_json"]) if row else _initial(invoice))
    finally:
        conn.close()


def save_review(tenant_id, invoice_id, payload, actor):
    try:
        draft = ReviewDraft.model_validate(payload)
    except ValidationError as exc:
        fields = "; ".join(".".join(map(str, error["loc"])) + ": " + error["msg"] for error in exc.errors()[:5])
        raise ReviewError("Invalid review: " + fields) from exc
    conn = get_conn(tenant_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        invoice = _invoice(conn, invoice_id)
        if invoice["status"] not in {"extracted", "reviewed"}:
            raise ReviewError("Invoice is not available for review", 409)
        row = conn.execute("SELECT version FROM supplier_invoice_reviews WHERE supplier_invoice_id=?", (invoice_id,)).fetchone()
        current = row["version"] if row else 0
        if draft.version != current:
            raise ReviewError("A newer draft exists. Reload it before saving; your edits have not been overwritten.", 409)
        products = {p["id"]: p for p in _products(conn)}
        for item in draft.items:
            if item.match_type == "existing":
                if item.matched_product_id not in products:
                    raise ReviewError("Selected product is unavailable in this shop")
            elif item.matched_product_id is not None:
                raise ReviewError("Only an explicitly selected existing product may have a product ID")
        saved = draft.model_dump(mode="json")
        saved["version"] = current + 1
        encoded = json.dumps(saved, allow_nan=False)
        conn.execute("""INSERT INTO supplier_invoice_reviews (supplier_invoice_id,version,draft_json,updated_by)
            VALUES (?,?,?,?) ON CONFLICT(supplier_invoice_id) DO UPDATE SET version=excluded.version,
            draft_json=excluded.draft_json,updated_by=excluded.updated_by,updated_at=datetime('now')""",
            (invoice_id, current + 1, encoded, actor))
        conn.execute("""INSERT INTO supplier_invoice_review_events (supplier_invoice_id,version,draft_json,actor)
            VALUES (?,?,?,?)""", (invoice_id, current + 1, encoded, actor))
        context = _context(conn, invoice, saved)
        conn.commit()
        return context
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
