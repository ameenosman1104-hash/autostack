import re, smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ..tenant_db import get_setting

DEFAULT_TEMPLATE = (
    "Hi {name},\n\n"
    "This is a friendly reminder that you have an outstanding balance of {amount} with {business}.\n\n"
    "Purchase date: {date}\n"
    "Amount due:    {amount}\n"
    "{products_line}"
    "\nPlease arrange payment at your earliest convenience.\n\n"
    "Thank you,\n{business}"
)


def _build_message(tid, debtor):
    business  = get_setting(tid, "business_name", "Our Business")
    date_str  = datetime.strptime(debtor["date_of_purchase"], "%Y-%m-%d").strftime("%d %b %Y")
    amount    = f"R {debtor['amount_owed']:,.2f}"
    products  = (debtor.get("products_owed") or "").strip()
    notes     = (debtor.get("notes") or "").strip()
    products_line = f"Products:      {products}\n" if products else ""

    template = get_setting(tid, "debtors_message_template", "").strip() or DEFAULT_TEMPLATE
    template = template.replace("{Products Owed}", "{products_line}")
    template = template.replace("{products owed}", "{products_line}")

    return template.format(
        name=debtor["name"], amount=amount, date=date_str,
        business=business, products=products or notes or "",
        products_line=products_line, notes=notes,
    )


def send_reminder(tid, debtor):
    message = _build_message(tid, debtor)
    method  = debtor.get("notify_method", "email")
    if method == "email":
        return _via_email(tid, debtor, message)
    elif method == "sms":
        return _via_sms(tid, debtor, message)
    elif method == "whatsapp":
        return _via_whatsapp(tid, debtor, message)
    return False, f"Unknown method: {method}"


def _extract_email(raw):
    m = re.search(r'[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}', raw)
    return m.group(0) if m else raw.strip()


def _via_email(tid, debtor, message):
    smtp_host = get_setting(tid, "email_smtp_host", "smtp.gmail.com")
    smtp_port = int(get_setting(tid, "email_smtp_port", "587"))
    sender    = get_setting(tid, "email_sender", "")
    password  = get_setting(tid, "email_password", "")
    to_email  = _extract_email(debtor.get("email", ""))

    if not sender:   return False, "Sender email not configured."
    if not password: return False, "Email password not configured."
    if not to_email: return False, f"No email for {debtor['name']}."

    business = get_setting(tid, "business_name", "Inventory Tracker")
    subject  = f"Payment Reminder — {business}"
    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.starttls()
        server.login(sender, password)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = to_email
        msg.attach(MIMEText(message, "plain"))
        server.sendmail(sender, to_email, msg.as_string())
        server.quit()
        return True, f"Email sent to {to_email}"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail auth failed — check App Password."
    except Exception as e:
        return False, str(e)


def _via_sms(tid, debtor, message):
    try:
        from twilio.rest import Client
        sid     = get_setting(tid, "twilio_account_sid", "")
        token   = get_setting(tid, "twilio_auth_token", "")
        from_no = get_setting(tid, "twilio_sms_number", "")
        phone   = debtor.get("phone", "").strip()
        if not sid or not token or not from_no:
            return False, "Twilio not configured."
        client = Client(sid, token)
        result = client.messages.create(body=message, from_=from_no, to=phone)
        return True, f"SMS sent (SID: {result.sid})"
    except Exception as e:
        return False, str(e)


def _via_whatsapp(tid, debtor, message):
    try:
        import requests, urllib.parse
        api_key = get_setting(tid, "callmebot_api_key", "")
        phone   = debtor.get("phone", "").strip().replace("+", "").replace(" ", "")
        if not api_key: return False, "CallMeBot API key not set."
        if not phone:   return False, f"No phone for {debtor['name']}."
        encoded = urllib.parse.quote(message)
        url  = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded}&apikey={api_key}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and "Message Sent" in resp.text:
            return True, "WhatsApp sent"
        return False, f"CallMeBot error: {resp.text[:100]}"
    except Exception as e:
        return False, str(e)
