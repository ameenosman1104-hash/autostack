import csv, io, base64, json
import requests as req_lib
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..tenant_db import (get_all_debtors, get_debtor, get_debtor_by_name,
                          add_debtor, update_debtor, delete_debtor,
                          delete_all_debtors, delete_debtors_by_ids,
                          next_reminder, save_setting, get_all_settings)
from datetime import date

debtors_bp = Blueprint("debtors", __name__)

FREQ_OPTIONS = [
    ("Every 3 days", 3), ("Every week", 7), ("Every 2 weeks", 14),
    ("Every month", 30), ("Every 2 months", 60), ("Every 3 months", 90),
]
DAYS_TO_LABEL = {d: l for l, d in FREQ_OPTIONS}


@debtors_bp.route("/")
@debtors_bp.route("/<filter>")
@login_required
def index(filter="active"):
    tid = current_user.tenant_id
    show_paid = filter in ("paid", "all")
    all_rows  = get_all_debtors(tid, show_paid=True)
    if filter == "active":
        rows = [d for d in all_rows if not d["is_paid"]]
    elif filter == "paid":
        rows = [d for d in all_rows if d["is_paid"]]
    else:
        rows = all_rows

    for d in rows:
        nxt, status = next_reminder(d)
        d["next_reminder"] = nxt
        d["reminder_status"] = status
        d["freq_label"] = DAYS_TO_LABEL.get(int(d.get("reminder_days", 14)), f"{d.get('reminder_days')} days")

    total_owed = sum(d["amount_owed"] for d in all_rows if not d["is_paid"])
    due_count  = sum(1 for d in rows if d.get("reminder_status") in ("overdue", "due_today"))
    settings   = get_all_settings(tid)
    has_saved_source = bool(settings.get("debtor_import_config", ""))
    return render_template("debtors.html", debtors=rows, filter=filter,
                           total_owed=total_owed, due_count=due_count,
                           active_count=sum(1 for d in all_rows if not d["is_paid"]),
                           has_saved_source=has_saved_source)


@debtors_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        try:
            add_debtor(
                current_user.tenant_id,
                name           = request.form["name"].strip(),
                phone          = request.form.get("phone", "").strip(),
                email          = request.form.get("email", "").strip(),
                amount_owed    = float(request.form.get("amount_owed", 0) or 0),
                date_of_purchase = request.form.get("date_of_purchase", date.today().isoformat()),
                notify_method  = request.form.get("notify_method", "email"),
                reminder_days  = int(request.form.get("reminder_days", 14)),
                notes          = request.form.get("notes", "").strip(),
                products_owed  = request.form.get("products_owed", "").strip(),
            )
            flash("Debtor added.", "success")
            return redirect(url_for("debtors.index"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("debtor_form.html", debtor=None,
                           freq_options=FREQ_OPTIONS, today=date.today().isoformat())


@debtors_bp.route("/<int:did>/edit", methods=["GET", "POST"])
@login_required
def edit(did):
    tid    = current_user.tenant_id
    debtor = get_debtor(tid, did)
    if not debtor:
        flash("Debtor not found.", "danger")
        return redirect(url_for("debtors.index"))
    if request.method == "POST":
        update_debtor(tid, did,
            name           = request.form["name"].strip(),
            phone          = request.form.get("phone", "").strip(),
            email          = request.form.get("email", "").strip(),
            amount_owed    = float(request.form.get("amount_owed", 0) or 0),
            date_of_purchase = request.form.get("date_of_purchase"),
            notify_method  = request.form.get("notify_method", "email"),
            reminder_days  = int(request.form.get("reminder_days", 14)),
            notes          = request.form.get("notes", "").strip(),
            products_owed  = request.form.get("products_owed", "").strip(),
        )
        flash("Debtor updated.", "success")
        return redirect(url_for("debtors.index"))
    return render_template("debtor_form.html", debtor=debtor,
                           freq_options=FREQ_OPTIONS, today=date.today().isoformat())


@debtors_bp.route("/<int:did>/mark-paid", methods=["POST"])
@login_required
def mark_paid(did):
    update_debtor(current_user.tenant_id, did, is_paid=1)
    flash("Marked as paid.", "success")
    return redirect(url_for("debtors.index"))


@debtors_bp.route("/<int:did>/delete", methods=["POST"])
@login_required
def delete(did):
    delete_debtor(current_user.tenant_id, did)
    flash("Debtor deleted.", "success")
    return redirect(url_for("debtors.index"))


_DEBTOR_GUESSES = {
    "name":             ["name", "customer name", "client name", "customer", "client", "full name", "debtor"],
    "phone":            ["phone", "cell", "mobile", "telephone", "contact number", "phone number", "cell number"],
    "email":            ["email", "e-mail", "email address", "mail"],
    "amount_owed":      ["amount", "amount owed", "balance", "debt", "owing", "total", "total owed", "balance due"],
    "date_of_purchase": ["date", "purchase date", "date of purchase", "sale date", "invoice date", "date of sale"],
    "products_owed":    ["products", "items", "product", "goods", "description", "product description", "products owed", "items owed"],
    "notes":            ["notes", "note", "comment", "comments", "remarks"],
}

def _debtor_guess(headers):
    result = {}
    for field, guesses in _DEBTOR_GUESSES.items():
        best, best_score = "", 0
        for h in headers:
            h_lc = h.lower().strip()
            for g in guesses:
                if g == h_lc:
                    score = 3          # exact match
                elif g in h_lc and "id" not in h_lc:
                    score = 2          # guess phrase is in header, not an ID column
                elif h_lc in g:
                    score = 1          # header is substring of guess phrase
                else:
                    score = 0
                if score > best_score:
                    best_score, best = score, h
        result[field] = best
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
            raise ValueError("No recognisable columns found.")
        rows = list(reader)
    else:
        reader   = csv.reader(io.StringIO(text), delimiter=delim)
        all_rows = [r for r in reader if any(c.strip() for c in r)]
        if not all_rows:
            raise ValueError("No data found.")
        headers = [f"Column {i+1}" for i in range(len(all_rows[0]))]
        rows    = [{headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))} for r in all_rows]
    return headers, rows

def _rows_from_xlsx(raw):
    import openpyxl
    wb   = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws   = wb.active
    data = list(ws.iter_rows(values_only=True))
    if not data:
        raise ValueError("Spreadsheet is empty.")
    headers = [str(c) if c is not None else "" for c in data[0]]
    rows    = [{headers[i]: (str(v) if v is not None else "") for i, v in enumerate(row)} for row in data[1:]]
    return headers, rows

def _rows_from_json(data, path=""):
    if path:
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key, data)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("No list found in API response. Try specifying a JSON key.")
    flat = []
    for item in data:
        if not isinstance(item, dict):
            continue
        flat.append({k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else "") for k, v in item.items()})
    if not flat:
        raise ValueError("No records found.")
    return list(flat[0].keys()), flat

def _pack(headers, rows):
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader(); w.writerows(rows)
    return base64.b64encode(buf.getvalue().encode()).decode()


def _get_dbt_connections(tid):
    return json.loads(get_all_settings(tid).get("saved_dbt_connections", "[]"))

def _save_dbt_connection(tid, name, cfg):
    conns = [c for c in _get_dbt_connections(tid) if c["name"] != name]
    conns.append({"name": name, **cfg})
    save_setting(tid, "saved_dbt_connections", json.dumps(conns))


@debtors_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_debtors():
    if request.method == "POST":
        phase = request.form.get("phase", "upload")

        if phase == "upload":
            tid          = current_user.tenant_id
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

            if use_saved:
                conns = _get_dbt_connections(tid)
                cfg = next((c for c in conns if c["name"] == use_saved), None)
                if not cfg:
                    flash("Saved connection not found.", "danger")
                    return redirect(url_for("debtors.import_debtors"))
                source = cfg["source"]
                merged = {**request.form, "csv_url": cfg.get("url",""),
                          "api_url": cfg.get("api_url",""), "api_key": cfg.get("api_key",""),
                          "api_path": cfg.get("api_path","")}
            else:
                merged = request.form

            try:
                if source == "file":
                    f = request.files.get("csv_file")
                    if not f or not f.filename:
                        raise ValueError("Please choose a file.")
                    raw = f.read()
                    if f.filename.lower().endswith((".xlsx", ".xls")):
                        headers, rows = _rows_from_xlsx(raw)
                    else:
                        headers, rows = _rows_from_text(raw.decode("utf-8-sig", errors="replace"), delimiter, has_header)
                elif source == "url":
                    url = merged.get("csv_url", "").strip()
                    if not url: raise ValueError("Please enter a URL.")
                    resp = req_lib.get(url, timeout=15, headers={"User-Agent": "DebtorImporter/1.0"})
                    resp.raise_for_status()
                    if "json" in resp.headers.get("Content-Type", ""):
                        headers, rows = _rows_from_json(resp.json())
                    else:
                        _assert_csv_text(resp.text, url)
                        headers, rows = _rows_from_text(resp.text, delimiter, has_header)
                elif source == "api":
                    api_url  = merged.get("api_url", "").strip()
                    api_key  = merged.get("api_key", "").strip()
                    api_path = merged.get("api_path", "").strip()
                    if not api_url: raise ValueError("Please enter an API URL.")
                    h = {"User-Agent": "DebtorImporter/1.0"}
                    if api_key: h["Authorization"] = f"Bearer {api_key}"
                    resp = req_lib.get(api_url, timeout=15, headers=h)
                    resp.raise_for_status()
                    if "json" in resp.headers.get("Content-Type", "") or api_path:
                        headers, rows = _rows_from_json(resp.json(), api_path)
                    else:
                        _assert_csv_text(resp.text, api_url)
                        headers, rows = _rows_from_text(resp.text, delimiter, has_header)
            except Exception as e:
                flash(str(e), "danger")
                return render_template("debtors_import.html", phase="upload",
                                       active_tab=source, connections=_get_dbt_connections(tid))

            if save_as and source in ("url", "api"):
                _save_dbt_connection(tid, save_as, {
                    "source":   source,
                    "url":      merged.get("csv_url",""),
                    "api_url":  merged.get("api_url",""),
                    "api_key":  merged.get("api_key",""),
                    "api_path": merged.get("api_path",""),
                })
                flash(f"Connection '{save_as}' saved.", "success")

            # Save source config so Refresh can re-use it
            if source in ("url", "api"):
                config = {
                    "source":   source,
                    "url":      request.form.get("csv_url", ""),
                    "api_url":  request.form.get("api_url", ""),
                    "api_key":  request.form.get("api_key", ""),
                    "api_path": request.form.get("api_path", ""),
                }
                save_setting(current_user.tenant_id, "debtor_import_config", json.dumps(config))

            # Auto-detect column mapping and skip straight to review/edit
            mapping = _debtor_guess(headers)
            save_setting(current_user.tenant_id, "debtor_import_mapping", json.dumps(mapping))

            def _v(col, row, default=""):
                return row.get(col, default).strip() if col else default

            mapped = []
            for row in rows:
                name = _v(mapping.get("name", ""), row)
                if not name:
                    continue
                amt_raw = _v(mapping.get("amount_owed", ""), row, "0").replace(",", "").lstrip("R$£€")
                try:    amt = f"{float(amt_raw):.2f}"
                except: amt = "0.00"
                mapped.append({
                    "name":             name,
                    "phone":            _v(mapping.get("phone", ""), row),
                    "email":            _v(mapping.get("email", ""), row),
                    "amount_owed":      amt,
                    "date_of_purchase": _v(mapping.get("date_of_purchase", ""), row) or date.today().isoformat(),
                    "products_owed":    _v(mapping.get("products_owed", ""), row),
                    "notes":            _v(mapping.get("notes", ""), row),
                    "notify_method":    "email",
                    "reminder_days":    "14",
                })

            if not mapped:
                flash("No valid rows found — make sure the sheet has a Name/Customer Name column.", "danger")
                return render_template("debtors_import.html", phase="upload", active_tab=source)

            rows_json = base64.b64encode(json.dumps(mapped).encode()).decode()
            flash(f"{len(mapped)} record(s) loaded. Review and edit before importing.", "info")
            return render_template("debtors_import.html", phase="edit",
                                   rows=mapped, rows_json=rows_json,
                                   freq_options=FREQ_OPTIONS, opts=opts, col=mapping)

        elif phase == "review":
            csv_b64 = request.form.get("csv_b64", "")
            try:
                text = base64.b64decode(csv_b64).decode("utf-8-sig", errors="replace")
            except Exception:
                flash("Upload data lost — please try again.", "danger")
                return redirect(url_for("debtors.import_debtors"))

            mapping = {k: request.form.get(f"map_{k}", "") for k in _DEBTOR_GUESSES}
            if not mapping["name"]:
                flash("You must map the Name column.", "danger")
                return redirect(url_for("debtors.import_debtors"))

            def _v(col, row, default=""):
                return row.get(col, default).strip() if col else default

            reader = csv.DictReader(io.StringIO(text))
            mapped = []
            for row in reader:
                name = _v(mapping["name"], row)
                if not name:
                    continue
                amt_raw = _v(mapping["amount_owed"], row, "0").replace(",", "").lstrip("R$£€")
                try:    amt = f"{float(amt_raw):.2f}"
                except: amt = "0.00"
                mapped.append({
                    "name":             name,
                    "phone":            _v(mapping["phone"], row),
                    "email":            _v(mapping["email"], row),
                    "amount_owed":      amt,
                    "date_of_purchase": _v(mapping["date_of_purchase"], row) or date.today().isoformat(),
                    "products_owed":    _v(mapping["products_owed"], row),
                    "notes":            _v(mapping["notes"], row),
                    "notify_method":    "email",
                    "reminder_days":    "14",
                })

            # Save mapping so Refresh can skip the mapping step
            save_setting(current_user.tenant_id, "debtor_import_mapping", json.dumps(mapping))

            rows_json = base64.b64encode(json.dumps(mapped).encode()).decode()
            return render_template("debtors_import.html", phase="edit",
                                   rows=mapped, rows_json=rows_json,
                                   freq_options=FREQ_OPTIONS)

        elif phase == "confirm":
            rows_json = request.form.get("rows_json", "")
            try:
                rows = json.loads(base64.b64decode(rows_json).decode())
            except Exception:
                flash("Data lost — please start over.", "danger")
                return redirect(url_for("debtors.import_debtors"))

            fields = ["name","phone","email","amount_owed","date_of_purchase",
                      "products_owed","notes","notify_method","reminder_days"]
            form_data = {f: request.form.getlist(f) for f in fields}
            dels      = set(request.form.getlist("delete_row"))

            dup_handling = request.form.get("dup_handling", "skip")
            date_fmt     = request.form.get("date_format", "%Y-%m-%d")
            tid = current_user.tenant_id

            from datetime import datetime as _dt
            def parse_date(d):
                if not d: return date.today().isoformat()
                for fmt in [date_fmt, "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]:
                    try: return _dt.strptime(d, fmt).strftime("%Y-%m-%d")
                    except: continue
                return d

            imported = skipped = 0
            for i, row in enumerate(rows):
                if str(i) in dels:
                    continue
                def fv(f): return (form_data[f][i] if i < len(form_data[f]) else row.get(f, "")).strip()
                name = fv("name")
                if not name:
                    skipped += 1
                    continue
                try:
                    amt_raw = fv("amount_owed").replace(",","").lstrip("R$£€")
                    kwargs = dict(
                        name             = name,
                        phone            = fv("phone"),
                        email            = fv("email"),
                        amount_owed      = float(amt_raw or 0),
                        date_of_purchase = parse_date(fv("date_of_purchase")),
                        products_owed    = fv("products_owed"),
                        notes            = fv("notes"),
                        notify_method    = fv("notify_method") or "email",
                        reminder_days    = int(fv("reminder_days") or 14),
                    )
                    if dup_handling == "overwrite":
                        existing = get_debtor_by_name(tid, name)
                        if existing:
                            update_debtor(tid, existing["id"], **{k: v for k, v in kwargs.items() if k != "name"})
                        else:
                            add_debtor(tid, **kwargs)
                    elif dup_handling == "add":
                        add_debtor(tid, **kwargs)
                    else:  # skip
                        existing = get_debtor_by_name(tid, name)
                        if not existing:
                            add_debtor(tid, **kwargs)
                        else:
                            skipped += 1
                            continue
                    imported += 1
                except Exception:
                    skipped += 1

            flash(f"Imported {imported} debtor(s).{' '+str(skipped)+' skipped.' if skipped else ''}", "success")
            return redirect(url_for("debtors.index"))

    return render_template("debtors_import.html", phase="upload",
                           connections=_get_dbt_connections(current_user.tenant_id))


@debtors_bp.route("/refresh-source")
@login_required
def refresh_source():
    tid      = current_user.tenant_id
    settings = get_all_settings(tid)
    raw_cfg  = settings.get("debtor_import_config", "")
    raw_map  = settings.get("debtor_import_mapping", "")

    if not raw_cfg:
        flash("No saved import source found. Please use Import first to set up a source.", "warning")
        return redirect(url_for("debtors.import_debtors"))

    try:
        cfg     = json.loads(raw_cfg)
        mapping = json.loads(raw_map) if raw_map else {}
    except Exception:
        flash("Saved source config is corrupted. Please re-import to reset it.", "danger")
        return redirect(url_for("debtors.import_debtors"))

    # Re-fetch from saved source
    try:
        source = cfg.get("source", "url")
        if source == "url":
            url = cfg.get("url", "")
            if not url:
                raise ValueError("No URL saved.")
            resp = req_lib.get(url, timeout=15, headers={"User-Agent": "DebtorImporter/1.0"})
            resp.raise_for_status()
            if "json" in resp.headers.get("Content-Type", ""):
                headers, rows = _rows_from_json(resp.json())
            else:
                _assert_csv_text(resp.text, url)
                headers, rows = _rows_from_text(resp.text)
        elif source == "api":
            api_url  = cfg.get("api_url", "")
            api_key  = cfg.get("api_key", "")
            api_path = cfg.get("api_path", "")
            if not api_url:
                raise ValueError("No API URL saved.")
            h = {"User-Agent": "DebtorImporter/1.0"}
            if api_key:
                h["Authorization"] = f"Bearer {api_key}"
            resp = req_lib.get(api_url, timeout=15, headers=h)
            resp.raise_for_status()
            if "json" in resp.headers.get("Content-Type", "") or api_path:
                headers, rows = _rows_from_json(resp.json(), api_path)
            else:
                _assert_csv_text(resp.text, api_url)
                headers, rows = _rows_from_text(resp.text)
        else:
            flash("Saved source is a file upload — please use Import and re-upload the file.", "warning")
            return redirect(url_for("debtors.import_debtors"))
    except Exception as e:
        flash(f"Refresh failed: {e}", "danger")
        return redirect(url_for("debtors.index"))

    # Apply saved mapping if we have it, otherwise re-guess
    if not mapping:
        mapping = _debtor_guess(headers)

    # Map rows using saved mapping
    def _v(col, row, default=""):
        return row.get(col, default).strip() if col else default

    mapped = []
    for row in rows:
        name = _v(mapping.get("name", ""), row)
        if not name:
            continue
        amt_raw = _v(mapping.get("amount_owed", ""), row, "0").replace(",", "").lstrip("R$£€")
        try:    amt = f"{float(amt_raw):.2f}"
        except: amt = "0.00"
        mapped.append({
            "name":             name,
            "phone":            _v(mapping.get("phone", ""), row),
            "email":            _v(mapping.get("email", ""), row),
            "amount_owed":      amt,
            "date_of_purchase": _v(mapping.get("date_of_purchase", ""), row) or date.today().isoformat(),
            "products_owed":    _v(mapping.get("products_owed", ""), row),
            "notes":            _v(mapping.get("notes", ""), row),
            "notify_method":    "email",
            "reminder_days":    "14",
        })

    rows_json = base64.b64encode(json.dumps(mapped).encode()).decode()
    flash(f"Refreshed from source — {len(mapped)} record(s) fetched. Review before importing.", "info")
    return render_template("debtors_import.html", phase="edit",
                           rows=mapped, rows_json=rows_json,
                           freq_options=FREQ_OPTIONS, col=mapping)


@debtors_bp.route("/delete-all", methods=["POST"])
@login_required
def bulk_delete_all():
    delete_all_debtors(current_user.tenant_id)
    return jsonify(ok=True, msg="All debtors deleted.")


@debtors_bp.route("/delete-selected", methods=["POST"])
@login_required
def bulk_delete_selected():
    data = request.get_json(silent=True) or {}
    ids  = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify(ok=False, msg="No debtors selected.")
    count = delete_debtors_by_ids(current_user.tenant_id, ids)
    return jsonify(ok=True, msg=f"{count} debtor(s) deleted.")


_ALLOWED_DBT_FIELDS = {'name','phone','email','amount_owed','date_of_purchase','products_owed','notes','notify_method','reminder_days'}

@debtors_bp.route("/<int:did>/update-field", methods=["POST"])
@login_required
def update_field(did):
    data  = request.get_json(silent=True) or {}
    field = data.get("field", "")
    value = data.get("value", "")
    if field not in _ALLOWED_DBT_FIELDS:
        return jsonify(ok=False, msg="Invalid field.")
    try:
        if field == 'amount_owed':
            value = float(str(value).replace(",", "").lstrip("R$£€") or 0)
        elif field == 'reminder_days':
            value = int(value)
        update_debtor(current_user.tenant_id, did, **{field: value})
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


@debtors_bp.route("/suggest")
@login_required
def suggest():
    q   = request.args.get("q", "").strip().lower()
    tid = current_user.tenant_id
    all_rows = get_all_debtors(tid, show_paid=False)
    hits = [d for d in all_rows if q in d["name"].lower()] if q else all_rows
    return jsonify([{
        "id":      d["id"],
        "name":    d["name"],
        "contact": d["email"] or d["phone"] or "",
        "amount":  d["amount_owed"],
        "method":  d["notify_method"],
    } for d in hits[:10]])


@debtors_bp.route("/<int:did>/send", methods=["POST"])
@login_required
def send_one(did):
    tid    = current_user.tenant_id
    debtor = get_debtor(tid, did)
    if not debtor:
        return jsonify(ok=False, msg="Not found")
    from ..services.debt_notifier import send_reminder
    ok, msg = send_reminder(tid, debtor)
    if ok:
        from datetime import datetime
        update_debtor(tid, did, last_reminded=datetime.now().strftime("%Y-%m-%d"))
    return jsonify(ok=ok, msg=msg)
