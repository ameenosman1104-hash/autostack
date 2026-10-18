"""Quotation output: shop profile, A4 PDF (black-and-white safe) and WhatsApp share link."""
import os
import re
import sqlite3
from io import BytesIO
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..tenant_db import get_setting

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg")
DEFAULT_COUNTRY_CODE = "27"  # South Africa; each shop can change it in quotation settings


class ShareError(ValueError):
    pass


def shop_profile(tid):
    from ..main_db import MAIN_DB
    name = ""
    try:
        with sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT business_name FROM tenants WHERE id=?", (int(tid),)).fetchone()
            name = row[0] if row and row[0] else ""
    except sqlite3.Error:
        name = ""
    return {
        "name": name or "Tyre Shop",
        "phone": get_setting(tid, "shop_phone", ""),
        "email": get_setting(tid, "shop_email", ""),
        "address": get_setting(tid, "shop_address", ""),
        "logo_path": get_setting(tid, "shop_logo_path", ""),
        "currency": get_setting(tid, "currency_symbol", "R") or "R",
        "country_code": re.sub(r"\D", "", get_setting(tid, "whatsapp_country_code", DEFAULT_COUNTRY_CODE) or ""),
    }


def logo_file(relative_path):
    """Return an absolute logo path only if it resolves inside app/static with an image extension."""
    if not relative_path:
        return None
    candidate = os.path.realpath(os.path.join(STATIC_DIR, relative_path))
    if not candidate.startswith(os.path.realpath(STATIC_DIR) + os.sep):
        return None
    if not candidate.lower().endswith(LOGO_EXTENSIONS) or not os.path.isfile(candidate):
        return None
    return candidate


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_money(value, symbol):
    return f"{symbol}{float(value):,.2f}"


def whatsapp_url(quotation, shop):
    phone = re.sub(r"\D", "", quotation.get("customer_phone") or "")
    if not phone:
        raise ShareError("Add the customer's phone number before sharing on WhatsApp")
    if phone.startswith("00"):
        phone = phone[2:]
    elif phone.startswith("0"):
        if not shop["country_code"]:
            raise ShareError("Set the WhatsApp country code in quotation settings first")
        phone = shop["country_code"] + phone[1:]
    text = (
        f"Hello {quotation['customer_name']},\n\n"
        f"Please find quotation {quotation['quotation_number']} from {shop['name']} "
        f"for {format_money(quotation['total'], shop['currency'])}.\n"
        f"This quotation is valid until {quotation['expiry_date']}.\n\n"
        "Thank you for choosing us."
    )
    return f"https://wa.me/{phone}?text={quote(text)}"


def render_pdf(quotation, items, shop):
    """Build an A4 PDF. Greyscale only so it prints cleanly on black-and-white printers."""
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=11)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#333333"))
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5)
    heading = ParagraphStyle("heading", parent=styles["Heading1"], fontSize=18, leading=22, spaceAfter=2)
    symbol = shop["currency"]
    frame_width = A4[0] - 30 * mm

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(15 * mm, 10 * mm, f"{quotation['quotation_number']}  |  {shop['name']}")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story = []
    header_left = [Paragraph(f"<b>{shop['name']}</b>", ParagraphStyle("shop", parent=body, fontSize=13, leading=16))]
    for line in (shop["address"], shop["phone"], shop["email"]):
        if line:
            header_left.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), small))
    logo = logo_file(shop["logo_path"])
    header_right = [Paragraph("<b>QUOTATION</b>", heading), Paragraph(f"No: {quotation['quotation_number']}", body),
                    Paragraph(f"Date: {quotation['quote_date']}", body),
                    Paragraph(f"Valid until: {quotation['expiry_date']}", body)]
    left_cell = header_left
    if logo:
        left_cell = [Image(logo, width=32 * mm, height=18 * mm, kind="proportional")] + header_left
    header = Table([[left_cell, header_right]], colWidths=[frame_width * 0.55, frame_width * 0.45])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story += [header, Spacer(1, 8 * mm)]

    customer_lines = [f"<b>To:</b> {esc(quotation['customer_name'])}"]
    if quotation.get("customer_phone"):
        customer_lines.append(esc(quotation["customer_phone"]))
    if quotation.get("customer_email"):
        customer_lines.append(esc(quotation["customer_email"]))
    story += [Paragraph("<br/>".join(customer_lines), body), Spacer(1, 6 * mm)]

    rows = [[Paragraph("<b>#</b>", cell), Paragraph("<b>Description</b>", cell), Paragraph("<b>Qty</b>", cell),
             Paragraph("<b>Unit price</b>", cell), Paragraph("<b>Disc %</b>", cell), Paragraph("<b>Total</b>", cell)]]
    for item in items:
        description = item["description"].replace("&", "&amp;").replace("<", "&lt;")
        if item.get("detail"):
            description += "<br/><font size=7.5>" + item["detail"].replace("&", "&amp;").replace("<", "&lt;") + "</font>"
        qty = int(item["quantity"]) if float(item["quantity"]).is_integer() else item["quantity"]
        rows.append([
            Paragraph(str(item["position"]), cell), Paragraph(description, cell), Paragraph(str(qty), cell),
            Paragraph(format_money(item["unit_price"], symbol), cell),
            Paragraph(f"{item['discount_pct']:g}", cell), Paragraph(format_money(item["line_total"], symbol), cell),
        ])
    table = Table(rows, colWidths=[9 * mm, frame_width * 0.42, 14 * mm, 26 * mm, 16 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 4 * mm))

    totals = Table([
        ["Subtotal", format_money(quotation["subtotal"], symbol)],
        ["Discount", "-" + format_money(quotation["discount_total"], symbol)],
        ["Total", format_money(quotation["total"], symbol)],
    ], colWidths=[40 * mm, 35 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(KeepTogether([totals, Spacer(1, 6 * mm)]))

    if quotation.get("notes"):
        story += [Paragraph("<b>Notes</b>", body), Paragraph(quotation["notes"].replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), body), Spacer(1, 4 * mm)]
    if quotation.get("terms"):
        story += [Paragraph("<b>Terms</b>", body), Paragraph(quotation["terms"].replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), small)]

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm,
                            title=f"Quotation {quotation['quotation_number']}", author=shop["name"])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
