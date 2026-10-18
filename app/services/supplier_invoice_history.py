"""Read-only, tenant-local supplier invoice history and original documents."""
import hashlib
import json
import warnings
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from math import ceil
from pathlib import Path

from app.tenant_db import get_conn
from app.services.supplier_invoice_review import ReviewError

STATUS_LABELS = {"uploaded":"Uploaded", "extracting":"Extracting", "extracted":"Extracted",
                 "reviewed":"Reviewed", "processed":"Processed", "failed":"Extraction failed"}
SORTS = {"supplier":"supplier_name COLLATE NOCASE", "invoice_number":"invoice_number COLLATE NOCASE",
         "invoice_date":"invoice_date", "upload_date":"upload_date", "total":"CAST(invoice_total AS REAL)",
         "status":"status", "uploader":"created_by COLLATE NOCASE"}


def _object(value):
    try:
        parsed=json.loads(value or "{}")
        return parsed if isinstance(parsed,dict) else {}
    except (ValueError,TypeError):
        return {}


def display_amount(value):
    if value is None:
        return "Not available"
    try:
        amount=Decimal(str(value))
        return format(amount,",.2f") if amount.is_finite() and abs(amount)<=Decimal("1000000000000") else "Not available"
    except (InvalidOperation,ValueError,TypeError):
        return "Not available"


def history(tenant_id, args):
    allowed={"q","supplier","invoice_number","date_from","date_to","status","sort","direction","page","per_page"}
    if any(key not in allowed or len(args.getlist(key))!=1 for key in args):
        raise ReviewError("Invalid history filter")
    filters={key:args.get(key,"").strip() for key in allowed}
    if any(len(filters[key])>200 for key in ("q","supplier","invoice_number")):
        raise ReviewError("Search text must be 200 characters or less")
    for key in ("date_from","date_to"):
        if filters[key]:
            try:
                if date.fromisoformat(filters[key]).isoformat()!=filters[key]:raise ValueError()
            except ValueError as exc:
                raise ReviewError("Date filters must use YYYY-MM-DD") from exc
    if filters["date_from"] and filters["date_to"] and filters["date_from"]>filters["date_to"]:
        raise ReviewError("Start date must not be after end date")
    if filters["status"] and filters["status"] not in STATUS_LABELS:
        raise ReviewError("Choose a valid processing status")
    sort=filters["sort"] or "upload_date"
    direction=filters["direction"] or "desc"
    if sort not in SORTS or direction not in {"asc","desc"}:
        raise ReviewError("Invalid history sort")
    try:
        if any(filters[k] and not filters[k].isascii() or filters[k] and not filters[k].isdigit() for k in ("page","per_page")):
            raise ValueError()
        page=int(filters["page"] or 1)
        per_page=int(filters["per_page"] or 20)
        if not 1<=page<=1000000 or per_page not in {10,20,50}:raise ValueError()
    except ValueError as exc:
        raise ReviewError("Choose a valid page and page size (10, 20 or 50)") from exc
    # Only explicit, known fields are selected. Internal paths never enter presentation data.
    sources={"r":"CASE WHEN json_valid(r.draft_json) THEN r.draft_json ELSE '{}' END",
             "x":"CASE WHEN json_valid(i.extracted_json) THEN i.extracted_json ELSE '{}' END",
             "c":"CASE WHEN json_valid(c.receipt_json) THEN c.receipt_json ELSE '{}' END"}
    def field(key,fallback):
        get=lambda alias:f"json_extract({sources[alias]}, '$.{key}')"
        return f"CASE WHEN c.supplier_invoice_id IS NOT NULL THEN COALESCE({get('c')},{get('r')},{fallback}) WHEN r.supplier_invoice_id IS NOT NULL THEN {get('r')} ELSE COALESCE({get('x')},{fallback}) END AS {key}"
    select=", ".join([field(key,"i."+key) for key in ("supplier_name","invoice_number","invoice_date")]+[field("invoice_total","NULL")])
    cte=f"""WITH listed AS (SELECT i.id,i.upload_date,i.created_by,i.status,r.version AS draft_version,
        {select} FROM supplier_invoices i
        LEFT JOIN supplier_invoice_reviews r ON r.supplier_invoice_id=i.id
        LEFT JOIN supplier_invoice_confirmations c ON c.supplier_invoice_id=i.id) """
    conditions,params=[],[]
    def contains(value):
        return "%"+value.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
    if filters["q"]:
        conditions.append("(supplier_name LIKE ? ESCAPE '\\' OR invoice_number LIKE ? ESCAPE '\\')")
        params.extend([contains(filters["q"])]*2)
    for key in ("supplier","invoice_number"):
        if filters[key]:
            conditions.append(f"{('supplier_name' if key=='supplier' else key)} LIKE ? ESCAPE '\\'")
            params.append(contains(filters[key]))
    for key,operator in (("date_from",">="),("date_to","<=")):
        if filters[key]:
            conditions.append(f"invoice_date {operator} ?")
            params.append(filters[key])
    if filters["status"]:
        conditions.append("status=?");params.append(filters["status"])
    where=" WHERE "+" AND ".join(conditions) if conditions else ""
    conn=get_conn(tenant_id)
    try:
        conn.execute("BEGIN")
        total=conn.execute(cte+"SELECT COUNT(*) FROM listed"+where,params).fetchone()[0]
        pages=max(1,ceil(total/per_page));page=min(page,pages)
        rows=conn.execute(cte+"SELECT * FROM listed"+where+f" ORDER BY {SORTS[sort]} {direction},id {direction} LIMIT ? OFFSET ?",[*params,per_page,(page-1)*per_page]).fetchall()
        invoices=[]
        for row in rows:
            item=dict(row);item["total_display"]=display_amount(item["invoice_total"])
            item["status_label"]=STATUS_LABELS.get(item["status"],"Unknown")
            invoices.append(item)
        filters.update(sort=sort,direction=direction,per_page=str(per_page))
        return {"invoices":invoices,"filters":filters,"total":total,"page":page,"pages":pages,"per_page":per_page,"statuses":STATUS_LABELS}
    finally:
        conn.rollback();conn.close()


def original_document(tenant_id, invoice_id):
    from app.routes.supplier_invoices import _get_invoice_storage_dir, MAX_FILE_SIZE, _verify_mime_type
    conn=get_conn(tenant_id)
    try:
        row=conn.execute("SELECT file_path,mime_type,file_size,extraction_sha256 FROM supplier_invoices WHERE id=?",(invoice_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ReviewError("Invoice not found",404)
    try:
        root=Path(_get_invoice_storage_dir(tenant_id)).resolve()
        path=Path(row["file_path"]).resolve()
        ext={"application/pdf":".pdf","image/jpeg":".jpg","image/png":".png"}.get(row["mime_type"])
        if not ext or not path.is_relative_to(root) or not path.is_file():raise ValueError()
        with path.open("rb") as stream:
            content=stream.read(MAX_FILE_SIZE+1)
        if not content or len(content)>MAX_FILE_SIZE or (row["file_size"] and len(content)!=row["file_size"]):raise ValueError()
        if not _verify_mime_type(BytesIO(content),ext)[0]:raise ValueError()
        if row["extraction_sha256"] and hashlib.sha256(content).hexdigest()!=row["extraction_sha256"]:raise ValueError()
        if ext==".pdf":
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(content),strict=True)
            if reader.is_encrypted or not 1<=len(reader.pages)<=20:raise ValueError()
            # Read every page's contents to catch damaged object streams before serving.
            for page in reader.pages:
                page.get_contents()
        else:
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter("error",Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    if image.format != ("PNG" if ext==".png" else "JPEG"):raise ValueError()
                    image.verify()
                with Image.open(BytesIO(content)) as image:
                    image.load()
        return content,row["mime_type"],ext
    except Exception as exc:
        raise ReviewError("The original document is unavailable or damaged. Please contact your administrator.",404) from exc


def invoice_details(tenant_id, invoice_id):
    conn=get_conn(tenant_id)
    try:
        conn.execute("BEGIN")
        row=conn.execute("""SELECT id,supplier_name,invoice_number,invoice_date,upload_date,status,created_by,
            confirmation_date,mime_type,extracted_json,extraction_error FROM supplier_invoices WHERE id=?""",(invoice_id,)).fetchone()
        if not row:raise ReviewError("Invoice not found",404)
        invoice=dict(row)
        def presentation(data):
            data=dict(data)
            if "items" in data:
                data["items"]=[item for item in data["items"] if isinstance(item,dict)] if isinstance(data["items"],list) else []
            return data
        extracted=presentation(_object(invoice.pop("extracted_json")))
        draft_row=conn.execute("SELECT version,draft_json,updated_by,updated_at FROM supplier_invoice_reviews WHERE supplier_invoice_id=?",(invoice_id,)).fetchone()
        draft=presentation(_object(draft_row["draft_json"])) if draft_row else None
        receipt_row=conn.execute("SELECT receipt_json,actor,created_at FROM supplier_invoice_confirmations WHERE supplier_invoice_id=?",(invoice_id,)).fetchone()
        receipt=_object(receipt_row["receipt_json"]) if receipt_row else None
        corrected=draft or extracted or {key:invoice[key] for key in ("supplier_name","invoice_number","invoice_date")}
        if receipt:
            corrected={**corrected,**{key:receipt[key] for key in ("supplier_name","invoice_number","invoice_date","subtotal","vat","invoice_total") if key in receipt}}
        movements=[]
        for line in (receipt or {}).get("lines",[]):
            movement=conn.execute("SELECT id,product_id,product_code,product_name,change_type,qty_before,qty_after,change_by,notes,created_at FROM stock_history WHERE id=? AND change_type='supplier_invoice'",(line.get("stock_history_id"),)).fetchone()
            # A receipt's explicit movement ID is authoritative; do not match invoice numbers by substring.
            if movement and movement["product_id"]==line.get("product_id"):
                movements.append(dict(movement))
        extraction_events=[dict(r) for r in conn.execute("SELECT actor,outcome,created_at FROM supplier_invoice_extraction_events WHERE supplier_invoice_id=? ORDER BY id",(invoice_id,))]
        review_events=[dict(r) for r in conn.execute("SELECT actor,version,created_at FROM supplier_invoice_review_events WHERE supplier_invoice_id=? ORDER BY version",(invoice_id,))]
        result={"invoice":invoice,"extracted":extracted,"corrected":corrected,"draft":dict(draft_row) if draft_row else None,
                "receipt":receipt,"confirmed_by":receipt_row["actor"] if receipt_row else None,
                "confirmed_at":receipt_row["created_at"] if receipt_row else invoice["confirmation_date"],
                "movements":movements,"extraction_events":extraction_events,"review_events":review_events,
                "status_label":STATUS_LABELS.get(invoice["status"],"Unknown"),"document_error":None}
    finally:
        conn.rollback();conn.close()
    try:
        original_document(tenant_id,invoice_id)
    except ReviewError as exc:
        result["document_error"]=str(exc)
    return result
