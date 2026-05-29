import time, re, io
from ..tenant_db import get_setting


def _build_message(tid, low_stock_items):
    business = get_setting(tid, "business_name", "Your Business")
    date_str = time.strftime("%d %b %Y, %I:%M %p")
    out  = [i for i in low_stock_items if i["current_stock"] <= 0]
    low  = [i for i in low_stock_items if i["current_stock"] > 0]
    lines = [f"INVENTORY ALERT - {business}", f"Generated: {date_str}", ""]
    if out:
        lines.append("OUT OF STOCK:")
        for i in out[:15]:
            lines.append(f"  x {i['name']} [{i['code']}] - Stock: {i['current_stock']}")
    if low:
        lines.append("\nLOW STOCK:")
        for i in low[:15]:
            lines.append(f"  ! {i['name']} [{i['code']}] - Stock: {i['current_stock']}, Min: {i['reorder_level']}")
    lines.append("\nPlease generate a Purchase Order in Inventory Tracker.")
    return "\n".join(lines)


def _build_pdf(tid, low_stock_items):
    """Generate a quotation request PDF and return it as bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    business  = get_setting(tid, "business_name", "Your Business")
    date_str  = time.strftime("%d %B %Y")
    ref_num   = time.strftime("QR-%Y%m%d-%H%M")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm)

    styles = getSampleStyleSheet()
    navy   = colors.HexColor("#1E293B")
    gold   = colors.HexColor("#F59E0B")
    light  = colors.HexColor("#F1F5F9")
    red    = colors.HexColor("#991B1B")
    amber  = colors.HexColor("#92400E")
    white  = colors.white

    title_style = ParagraphStyle("title", fontSize=20, textColor=white,
                                  fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=2)
    sub_style   = ParagraphStyle("sub", fontSize=9, textColor=colors.HexColor("#CBD5E1"),
                                  fontName="Helvetica", alignment=TA_CENTER, spaceAfter=0)
    label_style = ParagraphStyle("label", fontSize=9, textColor=colors.HexColor("#64748B"),
                                  fontName="Helvetica-Bold")
    value_style = ParagraphStyle("value", fontSize=10, textColor=navy, fontName="Helvetica")
    note_style  = ParagraphStyle("note", fontSize=8, textColor=colors.HexColor("#64748B"),
                                  fontName="Helvetica-Oblique")
    heading_style = ParagraphStyle("heading", fontSize=11, textColor=navy,
                                    fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=4)

    story = []

    # ── Header banner ──────────────────────────────────────────────
    hdr_data = [[Paragraph(business, title_style)],
                [Paragraph("QUOTATION REQUEST", sub_style)]]
    hdr_table = Table(hdr_data, colWidths=[170*mm])
    hdr_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), navy),
        ("TOPPADDING",    (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING",    (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 0, 0]),
    ]))
    story.append(hdr_table)

    # ── Gold accent bar ────────────────────────────────────────────
    story.append(Table([[""]], colWidths=[170*mm], rowHeights=[4*mm]))
    story[-1].setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), gold)]))

    story.append(Spacer(1, 6*mm))

    # ── Ref / Date meta ────────────────────────────────────────────
    meta = Table([
        [Paragraph("Reference No.", label_style), Paragraph(ref_num, value_style),
         Paragraph("Date Issued", label_style),   Paragraph(date_str, value_style)],
        [Paragraph("Requested By",  label_style), Paragraph(business, value_style),
         Paragraph("Priority",      label_style), Paragraph("URGENT — Restock Required", ParagraphStyle("urg", fontSize=10, textColor=red, fontName="Helvetica-Bold"))],
    ], colWidths=[35*mm, 55*mm, 35*mm, 45*mm])
    meta.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), light),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    story.append(meta)
    story.append(Spacer(1, 5*mm))

    # ── Section heading ────────────────────────────────────────────
    story.append(Paragraph("Items Requiring Quotation", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=gold, spaceAfter=4))

    # ── Items table ────────────────────────────────────────────────
    col_heads = ["#", "Product Name", "Code", "Current Qty", "Min Level", "Qty to Order", "Status"]
    col_w     = [10*mm, 58*mm, 28*mm, 22*mm, 22*mm, 25*mm, 22*mm]

    head_row = [Paragraph(h, ParagraphStyle("th", fontSize=8, textColor=white,
                fontName="Helvetica-Bold", alignment=TA_CENTER)) for h in col_heads]
    rows = [head_row]

    all_items = sorted(low_stock_items, key=lambda i: i["current_stock"])
    for idx, item in enumerate(all_items, 1):
        is_out    = item["current_stock"] <= 0
        qty_order = max(0, (item.get("reorder_level", 0) or 0) * 2 - item["current_stock"])
        status    = "OUT OF STOCK" if is_out else "LOW STOCK"
        clr       = red if is_out else amber

        rows.append([
            Paragraph(str(idx), ParagraphStyle("c", fontSize=8, alignment=TA_CENTER)),
            Paragraph(item["name"], ParagraphStyle("n", fontSize=8, fontName="Helvetica-Bold")),
            Paragraph(item.get("code",""), ParagraphStyle("c2", fontSize=7, textColor=colors.HexColor("#64748B"))),
            Paragraph(str(item["current_stock"]), ParagraphStyle("num", fontSize=8, alignment=TA_CENTER)),
            Paragraph(str(item.get("reorder_level","—")), ParagraphStyle("num2", fontSize=8, alignment=TA_CENTER)),
            Paragraph("", ParagraphStyle("blank", fontSize=8)),  # supplier fills in
            Paragraph(status, ParagraphStyle("st", fontSize=7, textColor=clr,
                      fontName="Helvetica-Bold", alignment=TA_CENTER)),
        ])

    item_table = Table(rows, colWidths=col_w, repeatRows=1)
    row_count  = len(rows)
    item_table.setStyle(TableStyle([
        # header
        ("BACKGROUND",    (0, 0), (-1, 0), navy),
        ("TOPPADDING",    (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        # data rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, light]),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, gold),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 6*mm))

    # ── Supplier section ───────────────────────────────────────────
    story.append(Paragraph("Supplier Response", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=gold, spaceAfter=6))

    # Use empty cells with a bottom border as write-in lines (cleaner than underscores)
    empty = ParagraphStyle("empty", fontSize=10)
    lbl   = label_style

    sig_data = [
        # Row headers
        [Paragraph("Supplier Name", lbl), Paragraph("", empty),
         Paragraph("Quote Valid Until", lbl), Paragraph("", empty)],
        # Row values (bottom-bordered cells act as write-in lines)
        [Paragraph("", empty), Paragraph("", empty),
         Paragraph("", empty), Paragraph("", empty)],

        [Paragraph("Contact Person", lbl), Paragraph("", empty),
         Paragraph("Total Amount (R)", lbl), Paragraph("", empty)],
        [Paragraph("", empty), Paragraph("", empty),
         Paragraph("", empty), Paragraph("", empty)],

        [Paragraph("Email / Phone", lbl), Paragraph("", empty),
         Paragraph("Signature", lbl), Paragraph("", empty)],
        [Paragraph("", empty), Paragraph("", empty),
         Paragraph("", empty), Paragraph("", empty)],
    ]
    sig_table = Table(sig_data, colWidths=[38*mm, 57*mm, 40*mm, 35*mm],
                      rowHeights=[7*mm, 10*mm, 7*mm, 10*mm, 7*mm, 10*mm])
    sig_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), light),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        # Outer border
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        # Bottom border on write-in rows (rows 1, 3, 5) — acts as the line to write on
        ("LINEBELOW",     (0, 1), (1, 1), 1, navy),
        ("LINEBELOW",     (2, 1), (3, 1), 1, navy),
        ("LINEBELOW",     (0, 3), (1, 3), 1, navy),
        ("LINEBELOW",     (2, 3), (3, 3), 1, navy),
        ("LINEBELOW",     (0, 5), (1, 5), 1, navy),
        ("LINEBELOW",     (2, 5), (3, 5), 1, navy),
        # Light dividers between label rows
        ("LINEBELOW",     (0, 0), (-1, 0), 0.3, colors.HexColor("#E2E8F0")),
        ("LINEBELOW",     (0, 2), (-1, 2), 0.3, colors.HexColor("#E2E8F0")),
        ("LINEBELOW",     (0, 4), (-1, 4), 0.3, colors.HexColor("#E2E8F0")),
        # Vertical separator between left and right field groups
        ("LINEBEFORE",    (2, 0), (2, -1), 0.5, colors.HexColor("#CBD5E1")),
    ]))
    story.append(sig_table)

    # ── Footer note ────────────────────────────────────────────────
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        f"This quotation request was automatically generated by {business}'s Inventory Tracker system on {date_str}. "
        "Please respond with pricing and availability at your earliest convenience.",
        note_style))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def send_low_stock_alert(tid, low_stock_items):
    message = _build_message(tid, low_stock_items)
    method  = get_setting(tid, "notification_method", "email")
    if method == "email":
        return _via_email(tid, message, low_stock_items)
    elif method == "callmebot":
        phone = get_setting(tid, "whatsapp_number", "")
        return _via_callmebot(phone, message)
    elif method == "both":
        ok1, msg1 = _via_email(tid, message, low_stock_items)
        phone = get_setting(tid, "whatsapp_number", "")
        ok2, msg2 = _via_callmebot(phone, message)
        ok = ok1 or ok2
        parts = []
        if ok1: parts.append(msg1)
        else:   parts.append(f"Email failed: {msg1}")
        if ok2: parts.append(msg2)
        else:   parts.append(f"WhatsApp failed: {msg2}")
        return ok, " | ".join(parts)
    return False, f"Unknown notification method: {method}"


def _via_email(tid, message, low_stock_items=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    smtp_host  = get_setting(tid, "email_smtp_host", "smtp.gmail.com")
    smtp_port  = int(get_setting(tid, "email_smtp_port", "587"))
    sender     = get_setting(tid, "email_sender", "")
    password   = get_setting(tid, "email_password", "")
    recipients = [r.strip() for r in get_setting(tid, "email_recipients", "").split(",") if r.strip()]

    if not sender:     return False, "Sender email not configured in Settings."
    if not password:   return False, "Email password not configured in Settings."
    if not recipients: return False, "No recipient email addresses configured."

    business = get_setting(tid, "business_name", "Inventory Tracker")
    subject  = f"⚠ Quotation Request — Low Stock Alert — {business}"

    # Build PDF attachment
    pdf_bytes = None
    if low_stock_items:
        try:
            pdf_bytes = _build_pdf(tid, low_stock_items)
        except Exception as pdf_err:
            pdf_bytes = None  # send without PDF rather than failing entirely

    ref = time.strftime("QR-%Y%m%d-%H%M")

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.starttls()
        server.login(sender, password)
        for to in recipients:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"]    = sender
            msg["To"]      = to

            # Plain-text body
            msg.attach(MIMEText(message, "plain"))

            # PDF attachment
            if pdf_bytes:
                pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
                pdf_part.add_header("Content-Disposition", "attachment",
                                    filename=f"Quotation_Request_{ref}.pdf")
                msg.attach(pdf_part)

            server.sendmail(sender, to, msg.as_string())
        server.quit()
        return True, f"Alert + PDF sent to {len(recipients)} recipient(s)"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail auth failed — check App Password in Settings."
    except Exception as e:
        return False, str(e)


def _via_callmebot(phone, message):
    try:
        import requests, urllib.parse
        phone_clean = phone.replace("+", "").replace(" ", "")
        encoded = urllib.parse.quote(message)
        url  = f"https://api.callmebot.com/whatsapp.php?phone={phone_clean}&text={encoded}&apikey="
        resp = requests.get(url, timeout=15)
        return True, "Sent via CallMeBot"
    except Exception as e:
        return False, str(e)
