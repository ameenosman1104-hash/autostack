import csv, io, base64, json
import requests as req_lib
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..tenant_db import (get_all_products, get_deleted_products, get_product, get_product_by_code,
                          add_product, update_product, delete_product,
                          delete_all_products, delete_products_by_ids,
                          count_products_with_manual_min, set_default_min_level,
                          save_setting, get_all_settings,
                          log_stock_change, auto_create_po_if_needed)

inventory_bp = Blueprint("inventory", __name__)


@inventory_bp.route("/")
@login_required
def index():
    search    = request.args.get("q", "")
    tid       = current_user.tenant_id
    products  = get_all_products(tid, search)
    settings  = get_all_settings(tid)
    raw_cfg      = settings.get("inv_import_config", "")
    has_source   = bool(raw_cfg)
    last_sync    = settings.get("inv_last_sync", "")
    source_headers = json.loads(settings.get("inv_source_headers", "[]"))
    col_mapping    = json.loads(settings.get("inv_col_mapping", "{}"))
    # reverse map: source column name → db field name
    col_to_field = {v: k for k, v in col_mapping.items() if v}
    # warn if stored URL is a slow published CSV (can't auto-upgrade without real sheet ID)
    slow_url = False
    if raw_cfg:
        try:
            _cfg = json.loads(raw_cfg)
            _u = _cfg.get("csv_url", "")
            slow_url = "docs.google.com" in _u and "/d/e/" in _u
        except Exception:
            pass
    # attach parsed extra_data to each product dict
    for p in products:
        try:
            p["_extra"] = json.loads(p.get("extra_data") or "{}")
        except Exception:
            p["_extra"] = {}
    return render_template("inventory.html", products=products, search=search,
                           has_source=has_source, last_sync=last_sync, slow_url=slow_url,
                           source_headers=source_headers, col_to_field=col_to_field)


@inventory_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        try:
            add_product(
                current_user.tenant_id,
                code           = request.form["code"].strip(),
                name           = request.form["name"].strip(),
                category       = request.form.get("category", "").strip(),
                unit           = request.form.get("unit", "PCS").strip(),
                current_stock  = float(request.form.get("current_stock", 0) or 0),
                reorder_level  = float(request.form.get("reorder_level", 0) or 0),
                last_cost_price= float(request.form.get("last_cost_price", 0) or 0),
                supplier       = request.form.get("supplier", "").strip(),
            )
            flash("Product added.", "success")
            return redirect(url_for("inventory.index"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("inventory_form.html", product=None)


@inventory_bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def edit(pid):
    tid     = current_user.tenant_id
    product = get_product(tid, pid)
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))
    if request.method == "POST":
        update_product(tid, pid,
            name           = request.form["name"].strip(),
            category       = request.form.get("category", "").strip(),
            unit           = request.form.get("unit", "PCS").strip(),
            current_stock  = float(request.form.get("current_stock", 0) or 0),
            reorder_level  = float(request.form.get("reorder_level", 0) or 0),
            last_cost_price= float(request.form.get("last_cost_price", 0) or 0),
            supplier       = request.form.get("supplier", "").strip(),
        )
        flash("Product updated.", "success")
        return redirect(url_for("inventory.index"))
    return render_template("inventory_form.html", product=product)


@inventory_bp.route("/<int:pid>/delete", methods=["POST"])
@login_required
def delete(pid):
    delete_product(current_user.tenant_id, pid)
    flash("Product deleted.", "success")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/<int:pid>/update-stock", methods=["POST"])
@login_required
def update_stock(pid):
    data = request.get_json()
    update_product(current_user.tenant_id, pid, current_stock=float(data.get("stock", 0)))
    return jsonify(ok=True)


@inventory_bp.route("/<int:pid>/update-min", methods=["POST"])
@login_required
def update_min(pid):
    data = request.get_json()
    # flag as manually set — user edited it directly from the table
    update_product(current_user.tenant_id, pid,
                   reorder_level=float(data.get("min", 0)),
                   min_level_manual=1)
    return jsonify(ok=True)


@inventory_bp.route("/suggest")
@login_required
def suggest():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    products = get_all_products(current_user.tenant_id, q)
    return jsonify([{
        "id":       p["id"],
        "name":     p["name"],
        "code":     p["code"],
        "category": p["category"],
        "stock":    p["current_stock"],
        "status":   ("out" if p["current_stock"] <= 0
                     else ("low" if p["reorder_level"] > 0 and p["current_stock"] <= p["reorder_level"]
                           else "ok")),
    } for p in products[:8]])


@inventory_bp.route("/suggest-all")
@login_required
def suggest_all():
    products = get_all_products(current_user.tenant_id)
    def status(p):
        if p["current_stock"] <= 0: return "out"
        if p["reorder_level"] > 0 and p["current_stock"] <= p["reorder_level"]: return "low"
        return "ok"
    return jsonify([{
        "id":     p["id"],
        "name":   p["name"],
        "code":   p["code"],
        "stock":  p["current_stock"],
        "status": status(p),
    } for p in products])


def _do_sync(tid):
    """Fetch from saved source and overwrite matching products. Returns (updated, added, skipped)."""
    settings = get_all_settings(tid)
    raw_cfg  = settings.get("inv_import_config", "")
    if not raw_cfg:
        raise ValueError("No saved import source. Import from a URL first.")
    cfg = json.loads(raw_cfg)
    headers, rows = _fetch_source(
        cfg["source"], cfg,
        cfg.get("delimiter", ","), cfg.get("has_header", True)
    )
    raw_map = settings.get("inv_import_mapping", "")
    saved   = json.loads(raw_map) if raw_map else {}
    guessed = _guess_mapping(headers)
    # Use saved mapping but fill in any empty fields from auto-guess
    mapping = {k: (saved.get(k, "") or guessed.get(k, "")) for k in guessed}

    # Build lookup of existing (non-deleted) products by code and name
    all_products = get_all_products(tid)
    by_code = {p["code"].lower(): p for p in all_products}
    by_name = {p["name"].lower(): p for p in all_products}

    # Track deleted product identifiers — sync must never restore these
    deleted = get_deleted_products(tid)
    deleted_codes = {p["code"].lower() for p in deleted}
    deleted_names = {p["name"].lower() for p in deleted}
    deleted_ids   = {p["id"] for p in deleted}

    def _val(col, row, default=""):
        return row.get(col, default).strip() if col else default
    def _fval(col, row):
        raw = _val(col, row, "").replace(",", "").lstrip("R$£€")
        try:    return float(raw)
        except: return None   # None = don't overwrite

    # Save source headers and mapping so the table display can mirror the sheet
    save_setting(tid, "inv_source_headers", json.dumps(headers))
    save_setting(tid, "inv_col_mapping", json.dumps(mapping))

    # Columns that are mapped to known DB fields — everything else goes into extra_data
    mapped_cols = set(v for v in mapping.values() if v)

    # Row-position → product-id map: lets us find renamed products by their row index
    raw_row_map = settings.get("inv_row_product_map", "{}")
    row_product_map = json.loads(raw_row_map) if raw_row_map else {}
    pid_lookup = {p["id"]: p for p in all_products}

    has_code_col = bool(mapping.get("code", ""))  # sheet has a real code column?
    updated = added = skipped = changed = 0
    for i, row in enumerate(rows):
        name = _val(mapping.get("name", ""), row)
        if not name:
            skipped += 1; continue
        sheet_code = _val(mapping.get("code", ""), row)
        code = sheet_code or name[:8].upper().replace(" ", "")

        # Collect extra columns (not mapped to any DB field)
        extra = {col: row.get(col, "") for col in headers if col not in mapped_cols}
        extra_json = json.dumps(extra)

        kwargs = {k: v for k, v in {
            "name":            name,
            "category":        _val(mapping.get("category",""), row) or None,
            "unit":            _val(mapping.get("unit",""), row) or None,
            "current_stock":   _fval(mapping.get("current_stock",""), row),
            "reorder_level":   _fval(mapping.get("reorder_level",""), row),
            "last_cost_price": _fval(mapping.get("last_cost_price",""), row),
            "supplier":        _val(mapping.get("supplier",""), row) or None,
            "extra_data":      extra_json,
        }.items() if v is not None}

        # Skip if name or code matches a deleted product — never restore deleted items
        if code.lower() in deleted_codes or name.lower() in deleted_names:
            skipped += 1; continue

        # Match 1: by code (only when sheet has a code column)
        # Match 2: by name (exact)
        # Match 3: by row position (handles renames — sheet row i → saved product id)
        if has_code_col:
            existing = by_code.get(code.lower()) or by_name.get(name.lower())
        else:
            existing = by_name.get(name.lower())
            if not existing and str(i) in row_product_map:
                pid_from_map = row_product_map[str(i)]
                if pid_from_map in deleted_ids:
                    skipped += 1; continue   # row maps to a deleted product — skip
                existing = pid_lookup.get(pid_from_map)

        # Also skip if the matched existing product was soft-deleted
        if existing and existing["id"] in deleted_ids:
            skipped += 1; continue

        if existing:
            # Sync ALL fields from sheet — sheet is the source of truth
            update_fields = {k: v for k, v in kwargs.items() if k != "code"}
            # Detect any real change: stock, name, extra columns, or anything else
            new_stock = update_fields.get("current_stock")
            stock_changed = new_stock is not None and abs(new_stock - existing.get("current_stock", 0)) > 0.001
            name_changed  = update_fields.get("name", "") != existing.get("name", "")
            extra_changed = update_fields.get("extra_data", "{}") != existing.get("extra_data", "{}")
            other_changed = any(
                str(update_fields.get(f, "")) != str(existing.get(f, ""))
                for f in ("category", "unit", "supplier", "reorder_level", "last_cost_price")
                if f in update_fields
            )
            if stock_changed or name_changed or extra_changed or other_changed:
                changed += 1
            if update_fields:
                update_product(tid, existing["id"], **update_fields)
            row_product_map[str(i)] = existing["id"]  # keep position map current
            # Refresh name index in case name just changed
            by_name[name.lower()] = existing
            updated += 1
        else:
            ok = False
            new_id = None
            for suffix in [""] + [f"_{j}" for j in range(1, 100)]:
                try:
                    new_id = add_product(tid, code=code + suffix, name=name,
                                category=kwargs.get("category",""), unit=kwargs.get("unit","PCS"),
                                current_stock=kwargs.get("current_stock",0),
                                reorder_level=kwargs.get("reorder_level",0),
                                last_cost_price=kwargs.get("last_cost_price",0),
                                supplier=kwargs.get("supplier",""),
                                extra_data=extra_json)
                    ok = True
                    break
                except Exception:
                    continue
            if ok:
                if new_id:
                    row_product_map[str(i)] = new_id
                added += 1
            else:
                skipped += 1

    from datetime import datetime as _dt
    save_setting(tid, "inv_last_sync", _dt.now().strftime("%Y-%m-%d %H:%M"))
    save_setting(tid, "inv_row_product_map", json.dumps(row_product_map))
    return updated, added, skipped, changed


@inventory_bp.route("/sync-debug")
@login_required
def sync_debug():
    """Debug: run the full sync logic dry-run and show what WOULD be updated."""
    try:
        tid = current_user.tenant_id
        settings = get_all_settings(tid)
        raw_cfg  = settings.get("inv_import_config", "")
        if not raw_cfg:
            return jsonify(error="No saved import source found.")
        cfg     = json.loads(raw_cfg)
        headers, rows = _fetch_source(cfg["source"], cfg, cfg.get("delimiter",","), cfg.get("has_header",True))
        raw_map = settings.get("inv_import_mapping", "")
        saved   = json.loads(raw_map) if raw_map else {}
        guessed = _guess_mapping(headers)
        mapping = {k: (saved.get(k,"") or guessed.get(k,"")) for k in guessed}
        all_products = get_all_products(tid)
        by_code = {p["code"].lower(): p for p in all_products}
        by_name = {p["name"].lower(): p for p in all_products}

        def _val(col, row, default=""):
            return row.get(col, default).strip() if col else default
        def _fval(col, row):
            raw = _val(col, row, "").replace(",","").lstrip("R$£€")
            try: return float(raw)
            except: return None

        has_code_col = bool(mapping.get("code",""))
        actions = []
        for row in rows:
            name = _val(mapping.get("name",""), row)
            if not name:
                actions.append({"name": "(blank)", "action": "SKIP"})
                continue
            code = _val(mapping.get("code",""), row) or name[:8].upper().replace(" ","")
            sheet_stock = _fval(mapping.get("current_stock",""), row)
            if has_code_col:
                existing = by_code.get(code.lower()) or by_name.get(name.lower())
            else:
                existing = by_name.get(name.lower())
            if existing:
                actions.append({
                    "name": name, "code": code,
                    "action": "UPDATE",
                    "db_stock": existing["current_stock"],
                    "sheet_stock": sheet_stock,
                    "would_change": sheet_stock is not None and abs(sheet_stock - existing["current_stock"]) > 0.001,
                    "found_by": "code" if by_code.get(code.lower()) else "name",
                })
            else:
                actions.append({"name": name, "code": code, "action": "ADD", "sheet_stock": sheet_stock})
        return jsonify(mapping=mapping, actions=actions)
    except Exception as e:
        import traceback
        return jsonify(error=str(e), trace=traceback.format_exc())


@inventory_bp.route("/refresh-source")
@login_required
def refresh_source():
    try:
        u, a, s, _ = _do_sync(current_user.tenant_id)
        flash(f"Synced from source — {u} updated, {a} added{', '+str(s)+' skipped' if s else ''}.", "success")
    except Exception as e:
        flash(f"Sync failed: {e}", "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/sync-source", methods=["POST"])
@login_required
def sync_source():
    try:
        tid = current_user.tenant_id
        u, a, s, chg = _do_sync(tid)
        reload_needed = (chg > 0 or a > 0)
        # Auto-PO if any stock just fell below minimum during this sync
        if chg > 0:
            auto_create_po_if_needed(tid)
        return jsonify(ok=True, updated=u, added=a, skipped=s,
                       changed=reload_needed, msg=f"{u} updated, {a} added")
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


_ALLOWED_INV_FIELDS = {'name','category','unit','current_stock','reorder_level','last_cost_price','supplier'}

@inventory_bp.route("/<int:pid>/update-field", methods=["POST"])
@login_required
def update_field(pid):
    data  = request.get_json(silent=True) or {}
    field = data.get("field", "")
    value = data.get("value", "")
    tid   = current_user.tenant_id
    try:
        if field.startswith("extra:"):
            # Update a column stored in extra_data JSON
            key  = field[6:]
            prod = get_product(tid, pid)
            if not prod:
                return jsonify(ok=False, msg="Product not found.")
            extra = json.loads(prod.get("extra_data") or "{}")
            extra[key] = str(value)
            update_product(tid, pid, extra_data=json.dumps(extra))
            return jsonify(ok=True)
        if field not in _ALLOWED_INV_FIELDS:
            return jsonify(ok=False, msg="Invalid field.")
        if field in ('current_stock', 'reorder_level', 'last_cost_price'):
            value = float(str(value).replace(",", "") or 0)
        # Log stock changes to audit trail
        if field == 'current_stock':
            prod = get_product(tid, pid)
            if prod:
                log_stock_change(tid, pid, prod["code"], prod["name"],
                                 "manual", prod["current_stock"], value,
                                 "Manual edit from inventory table")
        update_product(tid, pid, **{field: value})
        # Auto-PO if stock just fell to or below minimum
        if field == 'current_stock':
            auto_create_po_if_needed(tid)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


@inventory_bp.route("/delete-all", methods=["POST"])
@login_required
def bulk_delete_all():
    delete_all_products(current_user.tenant_id)
    return jsonify(ok=True, msg="All products deleted.")


@inventory_bp.route("/delete-selected", methods=["POST"])
@login_required
def bulk_delete_selected():
    data = request.get_json(silent=True) or {}
    ids  = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify(ok=False, msg="No products selected.")
    count = delete_products_by_ids(current_user.tenant_id, ids)
    return jsonify(ok=True, msg=f"{count} product(s) deleted.")


@inventory_bp.route("/check-manual-min")
@login_required
def check_manual_min():
    n = count_products_with_manual_min(current_user.tenant_id)
    return jsonify(manual_count=n)


@inventory_bp.route("/set-default-min", methods=["POST"])
@login_required
def set_default_min():
    data = request.get_json()
    try:
        min_level = float(data.get("min_level", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid number"), 400
    override = bool(data.get("override_manual", True))
    set_default_min_level(current_user.tenant_id, min_level, override)
    return jsonify(ok=True)


_FIELD_GUESSES = {
    "name":            ["product name", "item name", "name", "item", "description", "product", "title", "product_name"],
    "code":            ["product code", "item code", "sku", "code", "barcode", "ref", "reference", "part no", "part number", "part_no"],
    "category":        ["category", "cat", "type", "group", "department", "sub-category", "subcategory"],
    "unit":            ["unit of measure", "uom", "unit", "measure"],
    "current_stock":   ["stock qty", "stock quantity", "quantity on hand", "current stock", "on hand", "stock level", "stock", "qty", "quantity", "inventory"],
    "reorder_level":   ["reorder level", "min level", "minimum stock", "reorder point", "min", "minimum", "reorder"],
    "last_cost_price": ["cost price", "unit cost", "buying price", "purchase price", "cost", "price"],
    "supplier":        ["supplier name", "vendor name", "supplier", "vendor", "brand", "manufacturer"],
}

def _guess_mapping(headers):
    result = {}
    for field, guesses in _FIELD_GUESSES.items():
        matched = ""
        for h in headers:
            h_lc = h.lower().strip()
            for g in guesses:
                # exact match, or guess is contained in header, or header is contained in guess
                if g == h_lc or g in h_lc or h_lc in g:
                    matched = h
                    break
            if matched:
                break
        result[field] = matched
    return result


def _assert_csv_text(text, url=""):
    stripped = text.lstrip()
    if stripped.lower().startswith(("<!doctype", "<html")):
        if "google" in url.lower():
            raise ValueError(
                "Google returned a login page instead of CSV data. "
                "Fix: open the sheet → File → Share → Publish to the web → "
                "select 'Comma-separated values (.csv)' → click Publish. "
                "Use THAT URL (not the normal sharing link)."
            )
        raise ValueError(
            "The URL returned a web page (HTML) instead of CSV data. "
            "Make sure the link directly downloads a .csv file."
        )

def _rows_from_text(text, delimiter=",", has_header=True):
    delim = "\t" if delimiter == "tab" else delimiter
    if has_header:
        reader  = csv.DictReader(io.StringIO(text), delimiter=delim)
        headers = list(reader.fieldnames or [])
        if not headers:
            raise ValueError("No recognisable columns found in the file.")
        rows = list(reader)
    else:
        reader   = csv.reader(io.StringIO(text), delimiter=delim)
        all_rows = [r for r in reader if any(c.strip() for c in r)]
        if not all_rows:
            raise ValueError("No data found.")
        headers = [f"Column {i+1}" for i in range(len(all_rows[0]))]
        rows    = [{headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))} for r in all_rows]
    return headers, rows


def _rows_from_xlsx(file_bytes):
    """Parse Excel bytes → (headers, rows)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    data = list(ws.iter_rows(values_only=True))
    if not data:
        raise ValueError("The spreadsheet appears to be empty.")
    headers = [str(c) if c is not None else "" for c in data[0]]
    rows = []
    for row in data[1:]:
        rows.append({headers[i]: (str(v) if v is not None else "") for i, v in enumerate(row)})
    return headers, rows


def _rows_from_json(data, json_path=""):
    """
    Flatten a JSON API response to (headers, rows).
    json_path: dot-separated key path to the array, e.g. "data" or "result.items".
    """
    if json_path:
        for key in json_path.split("."):
            if isinstance(data, dict):
                data = data.get(key, data)
    # If still a dict (single object), wrap it
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("Could not find a list of records in the API response. Try specifying a JSON key.")
    # Flatten nested objects one level
    flat = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = {}
        for k, v in item.items():
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v)
            else:
                row[k] = str(v) if v is not None else ""
        flat.append(row)
    if not flat:
        raise ValueError("No records found in the API response.")
    headers = list(flat[0].keys())
    return headers, flat


def _pack(headers, rows):
    """Serialise rows back to CSV text for hidden field storage."""
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    w.writerows(rows)
    return base64.b64encode(buf.getvalue().encode()).decode()


def _get_inv_connections(tid):
    return json.loads(get_all_settings(tid).get("saved_inv_connections", "[]"))

def _save_inv_connection(tid, name, cfg):
    conns = [c for c in _get_inv_connections(tid) if c["name"] != name]
    conns.append({"name": name, **cfg})
    save_setting(tid, "saved_inv_connections", json.dumps(conns))

def _gsheets_realtime_url(url):
    """
    Convert any Google Sheets URL to the gviz/tq CSV endpoint, which has no
    server-side cache (changes appear within seconds).

    Handles:
      - Edit/view URLs:  .../spreadsheets/d/{ID}/edit#gid=123
      - Sharing URLs:    .../spreadsheets/d/{ID}/edit?usp=sharing
      - Published URLs:  .../spreadsheets/d/e/{KEY}/pub?... (cannot convert — no real ID)
    Returns the gviz URL string, or None if conversion isn't possible.
    """
    import re
    # Must be a Google Sheets URL
    if "docs.google.com/spreadsheets" not in url:
        return None
    # Published URLs use /d/e/{KEY}/ — we can't get the real ID from them
    if re.search(r'/d/e/', url):
        return None
    # Extract the real spreadsheet ID (after /d/, not followed by e/)
    m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]{20,})', url)
    if not m:
        return None
    sheet_id = m.group(1)
    # Extract gid (tab/sheet index) — default 0
    gid_m = re.search(r'[#&?]gid=(\d+)', url)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"


def _fetch_source(source, form, delimiter=",", has_header=True):
    """Fetch rows from url/api source. Returns (headers, rows)."""
    if source == "url":
        import time as _time
        url = form.get("csv_url", "").strip()
        if not url: raise ValueError("Please enter a URL.")
        # If this is a Google Sheets URL with a real ID, use the gviz endpoint —
        # it returns live data with no server-side cache (unlike /pub?output=csv).
        realtime = _gsheets_realtime_url(url)
        if realtime:
            url = realtime
        else:
            # Fallback: add cache-buster for non-gviz URLs
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}_cb={int(_time.time())}"
        resp = req_lib.get(url, timeout=15, headers={
            "User-Agent": "InventoryImporter/1.0",
            "Cache-Control": "no-cache",
        })
        resp.encoding = "utf-8"  # force UTF-8 — Google Sheets CSVs are always UTF-8
        resp.raise_for_status()
        if "json" in resp.headers.get("Content-Type", ""):
            return _rows_from_json(resp.json(), "")
        _assert_csv_text(resp.text, url)
        return _rows_from_text(resp.text, delimiter, has_header)
    elif source == "api":
        api_url  = form.get("api_url", "").strip()
        api_key  = form.get("api_key", "").strip()
        api_path = form.get("api_path", "").strip()
        if not api_url: raise ValueError("Please enter an API URL.")
        h = {"User-Agent": "InventoryImporter/1.0"}
        if api_key: h["Authorization"] = f"Bearer {api_key}"
        resp = req_lib.get(api_url, timeout=15, headers=h)
        resp.raise_for_status()
        if "json" in resp.headers.get("Content-Type", "") or api_path:
            return _rows_from_json(resp.json(), api_path)
        _assert_csv_text(resp.text, api_url)
        return _rows_from_text(resp.text, delimiter, has_header)
    raise ValueError("Unknown source.")


@inventory_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_csv():
    tid = current_user.tenant_id
    if request.method == "POST":
        phase = request.form.get("phase", "upload")

        if phase == "upload":
            source       = request.form.get("source", "file")
            delimiter    = request.form.get("delimiter", ",")
            has_header   = request.form.get("has_header", "1") == "1"
            date_fmt     = request.form.get("date_format", "%Y-%m-%d")
            dup_handling = request.form.get("dup_handling", "skip")
            save_as      = request.form.get("save_as", "").strip()
            use_saved    = request.form.get("use_saved", "").strip()
            opts = dict(delimiter=delimiter, has_header=has_header,
                        date_format=date_fmt, dup_handling=dup_handling)
            headers = rows = None

            # Load saved connection config if requested
            if use_saved:
                conns = _get_inv_connections(tid)
                cfg = next((c for c in conns if c["name"] == use_saved), None)
                if not cfg:
                    flash("Saved connection not found.", "danger")
                    return redirect(url_for("inventory.import_csv"))
                source = cfg["source"]
                request.form = request.form.copy()
                merged = {**request.form, "csv_url": cfg.get("url",""),
                          "api_url": cfg.get("api_url",""), "api_key": cfg.get("api_key",""),
                          "api_path": cfg.get("api_path","")}
            else:
                merged = request.form

            try:
                if source == "file":
                    f = request.files.get("csv_file")
                    if not f or not f.filename:
                        raise ValueError("Please choose a file to upload.")
                    raw = f.read()
                    fname = f.filename.lower()
                    if fname.endswith((".xlsx", ".xls")):
                        headers, rows = _rows_from_xlsx(raw)
                    else:
                        headers, rows = _rows_from_text(raw.decode("utf-8-sig", errors="replace"), delimiter, has_header)
                elif source in ("url", "api"):
                    headers, rows = _fetch_source(source, merged, delimiter, has_header)
                    # Save source so Refresh can re-use it
                    save_setting(tid, "inv_import_config", json.dumps({
                        "source":     source,
                        "csv_url":    merged.get("csv_url", ""),
                        "api_url":    merged.get("api_url", ""),
                        "api_key":    merged.get("api_key", ""),
                        "api_path":   merged.get("api_path", ""),
                        "delimiter":  delimiter,
                        "has_header": has_header,
                    }))

            except Exception as e:
                flash(str(e), "danger")
                return render_template("inventory_import.html", phase="upload",
                                       active_tab=source, connections=_get_inv_connections(tid))

            # Save connection for next time
            if save_as and source in ("url", "api"):
                _save_inv_connection(tid, save_as, {
                    "source":   source,
                    "url":      merged.get("csv_url",""),
                    "api_url":  merged.get("api_url",""),
                    "api_key":  merged.get("api_key",""),
                    "api_path": merged.get("api_path",""),
                })
                flash(f"Connection '{save_as}' saved.", "success")

            csv_b64 = _pack(headers, rows)
            guesses = _guess_mapping(headers)
            return render_template("inventory_import.html",
                                   phase="map", headers=headers,
                                   preview=rows, total=len(rows),
                                   csv_b64=csv_b64, guesses=guesses, opts=opts,
                                   connections=_get_inv_connections(tid))

        elif phase == "review":
            # Apply column mapping → produce clean rows → show editable grid
            csv_b64 = request.form.get("csv_b64", "")
            try:
                text = base64.b64decode(csv_b64).decode("utf-8-sig", errors="replace")
            except Exception:
                flash("Upload data lost — please try again.", "danger")
                return redirect(url_for("inventory.import_csv"))

            mapping = {
                "name":            request.form.get("map_name", ""),
                "code":            request.form.get("map_code", ""),
                "category":        request.form.get("map_category", ""),
                "unit":            request.form.get("map_unit", ""),
                "current_stock":   request.form.get("map_stock", ""),
                "reorder_level":   request.form.get("map_min", ""),
                "last_cost_price": request.form.get("map_cost", ""),
                "supplier":        request.form.get("map_supplier", ""),
            }

            if not mapping["name"]:
                flash("You must map the Product Name column before continuing.", "danger")
                return redirect(url_for("inventory.import_csv"))
            save_setting(tid, "inv_import_mapping", json.dumps(mapping))

            def _val(col, row, default=""):
                return row.get(col, default).strip() if col else default

            def _fval(col, row):
                raw = _val(col, row, "0").replace(",", "").lstrip("R$£€")
                try:    return f"{float(raw):.2f}"
                except: return "0.00"

            reader = csv.DictReader(io.StringIO(text))
            mapped_rows = []
            for row in reader:
                name = _val(mapping["name"], row)
                if not name:
                    continue
                mapped_rows.append({
                    "name":            name,
                    "code":            _val(mapping["code"], row) or name[:8].upper().replace(" ", ""),
                    "category":        _val(mapping["category"], row),
                    "unit":            _val(mapping["unit"], row) or "PCS",
                    "current_stock":   _fval(mapping["current_stock"], row),
                    "reorder_level":   _fval(mapping["reorder_level"], row),
                    "last_cost_price": _fval(mapping["last_cost_price"], row),
                    "supplier":        _val(mapping["supplier"], row),
                })

            opts = {
                "date_format":  request.form.get("date_format", "%Y-%m-%d"),
                "dup_handling": request.form.get("dup_handling", "skip"),
            }
            rows_json = base64.b64encode(json.dumps(mapped_rows).encode()).decode()
            return render_template("inventory_import.html",
                                   phase="edit", rows=mapped_rows,
                                   rows_json=rows_json, opts=opts, col=mapping)

        elif phase == "confirm":
            rows_json = request.form.get("rows_json", "")
            try:
                rows = json.loads(base64.b64decode(rows_json).decode())
            except Exception:
                flash("Data lost — please start over.", "danger")
                return redirect(url_for("inventory.import_csv"))

            # Overlay any edits from the form
            names   = request.form.getlist("name")
            codes   = request.form.getlist("code")
            cats    = request.form.getlist("category")
            units   = request.form.getlist("unit")
            stocks  = request.form.getlist("current_stock")
            mins    = request.form.getlist("reorder_level")
            costs   = request.form.getlist("last_cost_price")
            supps   = request.form.getlist("supplier")
            dels    = set(request.form.getlist("delete_row"))

            def sf(v):
                try:    return float(str(v).replace(",", "").lstrip("R$£€") or 0)
                except: return 0.0

            dup_handling = request.form.get("dup_handling", "overwrite")
            tid = current_user.tenant_id

            # Build name lookup once for overwrite/skip matching
            all_existing   = get_all_products(tid)
            by_code_lookup = {p["code"].lower(): p for p in all_existing}
            by_name_lookup = {p["name"].lower(): p for p in all_existing}

            imported = skipped = 0
            for i, row in enumerate(rows):
                if str(i) in dels:
                    continue
                name = (names[i] if i < len(names) else row["name"]).strip()
                if not name:
                    skipped += 1
                    continue
                code = (codes[i] if i < len(codes) else row["code"]).strip() or name[:8].upper().replace(" ", "")
                kwargs = dict(
                    name           = name,
                    code           = code,
                    category       = (cats[i]  if i < len(cats)  else row["category"]).strip(),
                    unit           = (units[i] if i < len(units) else row["unit"]).strip() or "PCS",
                    current_stock  = sf(stocks[i] if i < len(stocks) else row["current_stock"]),
                    reorder_level  = sf(mins[i]   if i < len(mins)   else row["reorder_level"]),
                    last_cost_price= sf(costs[i]  if i < len(costs)  else row["last_cost_price"]),
                    supplier       = (supps[i]  if i < len(supps)  else row["supplier"]).strip(),
                )
                # Find existing product by code first, then by name
                existing = by_code_lookup.get(code.lower()) or by_name_lookup.get(name.lower())
                try:
                    if dup_handling == "overwrite":
                        if existing:
                            # Only update operational fields — never change code or name
                            update_fields = {k: v for k, v in kwargs.items() if k not in ("code", "name")}
                            update_product(tid, existing["id"], **update_fields)
                        else:
                            # New product — try with suffix if code already taken
                            for suffix in [""] + [f"_{j}" for j in range(1, 100)]:
                                try:
                                    add_product(tid, **{**kwargs, "code": code + suffix})
                                    break
                                except Exception:
                                    continue
                        imported += 1
                    elif dup_handling == "add":
                        for suffix in [""] + [f"_{j}" for j in range(1, 100)]:
                            try:
                                add_product(tid, **{**kwargs, "code": code + suffix})
                                imported += 1
                                break
                            except Exception:
                                continue
                        else:
                            skipped += 1
                    else:  # skip — only add if truly new
                        if existing:
                            skipped += 1
                        else:
                            for suffix in [""] + [f"_{j}" for j in range(1, 100)]:
                                try:
                                    add_product(tid, **{**kwargs, "code": code + suffix})
                                    imported += 1
                                    break
                                except Exception:
                                    continue
                            else:
                                skipped += 1
                except Exception:
                    skipped += 1

            msg = f"Successfully imported {imported} product(s)."
            if skipped:
                msg += f" {skipped} row(s) skipped."
            flash(msg, "success")
            return redirect(url_for("inventory.index"))

    return render_template("inventory_import.html", phase="upload",
                           connections=_get_inv_connections(tid))
