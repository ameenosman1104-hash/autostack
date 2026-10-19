"""Job card output: greyscale A4 PDF, South African WhatsApp links. All user text is escaped.

Nothing here sends a message. WhatsApp links only open a chat with the text ready to send.
"""
import re
from datetime import date
from io import BytesIO
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .quotation_output import format_money, logo_file

STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "ready": "Ready for collection",
    "completed": "Collected",
    "cancelled": "Cancelled",
}
STATUS_NOTES = {
    "open": "Your job card has been opened. We will keep you updated.",
    "in_progress": "We are working on your vehicle.",
    "ready": "Your vehicle is ready for collection.",
    "completed": "Thank you for collecting your vehicle.",
    "cancelled": "This job card has been cancelled. Please contact us if you have questions.",
}
LONG_WORD = 60
DASH = "—"


class PhoneError(ValueError):
    """The customer's number cannot be turned into a WhatsApp link. Safe to show to the user."""


def esc(text):
    """Escape user text for ReportLab paragraphs and keep long unbroken words from overflowing."""
    value = str(text if text is not None else "")
    value = re.sub(r"(\S{%d})(?=\S)" % LONG_WORD, r"\1 ", value)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def normalise_phone(raw, country_code="27"):
    """Return an international number with no '+', for example 27821234567.

    Accepts 082 123 4567, +27 82 123 4567, +27 (0)82 123 4567, 0027 82 123 4567 and 27821234567.
    South African numbers must have 9 digits after the country code.
    """
    value = str(raw or "").strip()
    if not value:
        raise PhoneError("Add the customer's phone number before sending a WhatsApp update")
    if re.search(r"[^0-9+\s().\-]", value):
        raise PhoneError("The phone number contains characters that cannot be used")
    digits = re.sub(r"\D", "", value)
    cc = re.sub(r"\D", "", str(country_code or ""))
    if value.startswith("+"):
        international = digits
    elif value.startswith("00"):
        international = digits[2:]
    elif digits.startswith("0"):
        if not cc:
            raise PhoneError("Set the WhatsApp country code in quotation settings first")
        international = cc + digits[1:]
    else:
        international = digits
    if cc == "27" and international.startswith("270"):
        international = "27" + international[3:]  # "+27 (0)82..." uses the trunk zero after the code
    if international.startswith("27"):
        if len(international) != 11:
            raise PhoneError("A South African number needs 9 digits after +27")
    elif not (8 <= len(international) <= 15):
        raise PhoneError("The phone number length is not valid")
    return international


def job_whatsapp_url(detail, shop):
    header = detail["header"]
    # Prefer the customer's current saved number; fall back to the copy taken when the job was opened.
    phone = header.get("current_phone") or header.get("customer_phone")
    number = normalise_phone(phone, shop.get("country_code", "27"))
    status = STATUS_LABELS.get(header["status"], header["status"])
    text = (
        f"Hello {header.get('customer_name') or 'there'},\n\n"
        f"Update from {shop.get('name', 'our workshop')} about your vehicle {header.get('registration', '')}.\n"
        f"Job card: {header['job_number']}\n"
        f"Status: {status}\n\n"
        f"{STATUS_NOTES.get(header['status'], '')}\n\n"
        "Thank you for choosing us."
    )
    return f"https://wa.me/{number}?text={quote(text)}"


def _or_dash(value):
    return esc(value) if value not in (None, "") else DASH


def render_job_card_pdf(detail, shop):
    """Build an A4 job card. Greyscale only. Tables repeat their headers across pages."""
    h = detail["header"]
    items = detail["items"]
    symbol = shop.get("currency") or "R"
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=11.5)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#333333"))
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5)
    label = ParagraphStyle("label", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
    heading = ParagraphStyle("heading", parent=styles["Heading1"], fontSize=18, leading=22, spaceAfter=2)
    section = ParagraphStyle("section", parent=body, fontName="Helvetica-Bold", fontSize=9.5, spaceBefore=4, spaceAfter=3)
    width = A4[0] - 30 * mm

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(15 * mm, 10 * mm, f"{h['job_number']}  |  {shop.get('name', '')}")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story = []
    left = []
    logo = logo_file(shop.get("logo_path", ""))
    if logo:
        left.append(Image(logo, width=32 * mm, height=18 * mm, kind="proportional"))
    left.append(Paragraph(f"<b>{esc(shop.get('name', 'Tyre Shop'))}</b>", ParagraphStyle("shop", parent=body, fontSize=13, leading=16)))
    for line in (shop.get("address"), shop.get("phone"), shop.get("email")):
        if line:
            left.append(Paragraph(esc(line), small))
    opened = (h.get("opened_at") or "")[:10] or DASH
    right = [Paragraph("<b>JOB CARD</b>", heading), Paragraph(f"No: {esc(h['job_number'])}", body),
             Paragraph(f"Opened: {esc(opened)}", body),
             Paragraph(f"Promised: {_or_dash(h.get('promised_date'))}", body),
             Paragraph(f"Status: {esc(STATUS_LABELS.get(h['status'], h['status']))}", body)]
    header = Table([[left, right]], colWidths=[width * 0.55, width * 0.45])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story += [header, Spacer(1, 6 * mm)]

    customer = [Paragraph("<b>Customer</b>", label), Paragraph(esc(h.get("customer_name")) or DASH, body),
                Paragraph(_or_dash(h.get("customer_phone")), body)]
    if h.get("customer_email"):
        customer.append(Paragraph(esc(h["customer_email"]), body))
    vehicle_name = " ".join(p for p in (h.get("vehicle_make"), h.get("vehicle_model")) if p) or None
    mileage = f"{h['mileage_in']} km" if h.get("mileage_in") is not None else DASH
    if h.get("mileage_out") is not None:
        mileage += f" in / {h['mileage_out']} km out"
    vehicle = [Paragraph("<b>Vehicle</b>", label), Paragraph(f"<b>{_or_dash(h.get('registration'))}</b>", body),
               Paragraph(f"Make / model: {_or_dash(vehicle_name)}", body),
               Paragraph(f"Year: {_or_dash(h.get('vehicle_year'))}", body),
               Paragraph(f"Mileage: {esc(mileage) if mileage != DASH else DASH}", body),
               Paragraph(f"Technician: {esc(h['technician']) if h.get('technician') else 'Not assigned'}", body)]
    details = Table([[customer, vehicle]], colWidths=[width * 0.5, width * 0.5])
    details.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#777777")),
                                 ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#777777")),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story += [details, Spacer(1, 4 * mm)]

    story.append(Paragraph("Customer complaint", section))
    story.append(Paragraph(esc(h.get("customer_complaint")) if h.get("customer_complaint") else "Not recorded", body))

    story.append(Paragraph("Tyres and services", section))
    rows = [[Paragraph("<b>#</b>", cell), Paragraph("<b>Description</b>", cell), Paragraph("<b>Qty</b>", cell),
             Paragraph("<b>Price</b>", cell), Paragraph("<b>Disc %</b>", cell), Paragraph("<b>Total</b>", cell)]]
    for item in items:
        description = esc(item.get("description"))
        if item.get("detail"):
            description += "<br/><font size=7.5>" + esc(item["detail"]) + "</font>"
        qty = int(item["quantity"]) if float(item["quantity"]).is_integer() else item["quantity"]
        rows.append([Paragraph(str(item["position"]), cell), Paragraph(description, cell), Paragraph(str(qty), cell),
                     Paragraph(format_money(item["unit_price"], symbol), cell),
                     Paragraph(f"{item['discount_pct']:g}", cell), Paragraph(format_money(item["line_total"], symbol), cell)])
    if not items:
        rows.append([Paragraph("", cell), Paragraph("No items recorded", cell), "", "", "", ""])
    table = Table(rows, colWidths=[9 * mm, width * 0.42, 14 * mm, 26 * mm, 16 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [table, Spacer(1, 3 * mm)]

    totals = Table([["Subtotal", format_money(h["subtotal"], symbol)],
                    ["Discount", "-" + format_money(h["discount_total"], symbol)],
                    ["Total", format_money(h["total"], symbol)]], colWidths=[40 * mm, 35 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                                ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
                                ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold")]))
    story += [KeepTogether([totals, Spacer(1, 4 * mm)])]

    story.append(Paragraph("Work performed", section))
    story.append(Paragraph(esc(h.get("work_performed")) if h.get("work_performed") else "Not recorded", body))
    if h.get("notes"):
        story.append(Paragraph("Internal notes", section))
        story.append(Paragraph(esc(h["notes"]), small))

    signature_row = [[Paragraph("Customer signature", label), Paragraph("Technician signature", label)],
                     ["", ""],
                     [Paragraph("Date: ____________________", label), Paragraph("Date: ____________________", label)]]
    signatures = Table(signature_row, colWidths=[width * 0.5, width * 0.5], rowHeights=[None, 16 * mm, None])
    signatures.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
                                    ("LINEABOVE", (1, 1), (1, 1), 0.6, colors.black),
                                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    story += [Spacer(1, 6 * mm), KeepTogether([signatures])]

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm,
                            title=f"Job card {h['job_number']}", author=shop.get("name", ""))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
