import re, smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ..tenant_db import get_setting, outstanding_balance

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
    amount    = f"R {outstanding_balance(debtor):,.2f}"
    products  = (debtor.get("products_owed") or "").strip()
    notes     = (debtor.get("notes") or "").strip()
    products_line = f"Products:      {products}\n" if products else ""

    template = get_setting(tid, "debt_reminder_template", "").strip() or DEFAULT_TEMPLATE
    template = template.replace("{Products Owed}", "{products_line}")
    template = template.replace("{products owed}", "{products_line}")

    return template.format(
        name=debtor["name"], amount=amount, date=date_str,
        business=business, products=products or notes or "",
        products_line=products_line, notes=notes,
    )


def send_reminder(tid, debtor):
    if outstanding_balance(debtor) <= 0:
        return False, "No outstanding balance; reminder not sent."
    try:
        message = _build_message(tid, debtor)
    except (ValueError, KeyError):
        return False, "Invalid reminder template or purchase date. Check Settings."
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
    """Route email through Gmail OAuth if connected, otherwise use SMTP."""
    # Check if Gmail OAuth is connected
    from ..gmail_oauth import get_authorized_email
    gmail_email = get_authorized_email(tid)

    if gmail_email:
        return _via_gmail(tid, debtor, message, gmail_email)
    else:
        return _via_smtp(tid, debtor, message)

def _via_smtp(tid, debtor, message):
    smtp_host = get_setting(tid, "email_smtp_host", "smtp.gmail.com")
    smtp_port = int(get_setting(tid, "email_smtp_port", "587"))
    sender    = get_setting(tid, "email_sender", "")
    password  = get_setting(tid, "email_password", "")
    to_email  = _extract_email(debtor.get("email", ""))

    # Diagnostic logging (NO PASSWORD LOGGING)
    print(f"[DEBT-NOTIFIER] Loaded sender: {sender}, password exists: {bool(password)}, to_email: {to_email}")

    if not sender:
        print(f"[DEBT-NOTIFIER] FAIL: No sender email configured")
        return False, "Sender email not configured. Check Settings > Email Settings."
    if not password:
        print(f"[DEBT-NOTIFIER] FAIL: No password configured")
        return False, "Gmail App Password not configured. Check Settings > Email Settings."
    if not to_email:
        print(f"[DEBT-NOTIFIER] FAIL: No email for debtor {debtor['name']}")
        return False, f"No email address for {debtor['name']}."

    business = get_setting(tid, "business_name", "Inventory Tracker")
    subject  = f"Payment Reminder — {business}"

    try:
        import ssl
        # Create secure SSL context that verifies certificates
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.starttls(context=context)
        server.login(sender, password)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = to_email
        msg.attach(MIMEText(message, "plain"))
        server.sendmail(sender, to_email, msg.as_string())
        server.quit()
        return True, f"Email sent to {to_email}"

    except smtplib.SMTPAuthenticationError as e:
        return False, "Gmail authentication failed. Check that you're using a 16-character App Password, not your regular Gmail password. See Settings > Email Settings for instructions."
    except smtplib.SMTPException as e:
        error_str = str(e)
        if "535" in error_str:  # 535 = auth failed
            return False, "Gmail authentication failed. Verify your App Password is correct."
        elif "Connection refused" in error_str:
            return False, "Cannot connect to Gmail SMTP server. Check your internet connection."
        else:
            return False, f"SMTP error: {error_str}"
    except Exception as e:
        error_str = str(e)
        if "timed out" in error_str.lower():
            return False, "Connection timed out. Check your internet connection."
        elif "invalid" in error_str.lower() and "@" in to_email:
            return False, f"Invalid email address: {to_email}"
        else:
            return False, f"Email error: {error_str}"


def _via_gmail(tid, debtor, message, sender):
    """Send email via Gmail API."""
    try:
        from ..gmail_oauth import get_gmail_token
        import base64
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google.auth.exceptions import RefreshError
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        to_email = _extract_email(debtor.get("email", ""))
        if not to_email:
            return False, f"No email address for {debtor['name']}."

        # Get stored token
        token_data = get_gmail_token(tid)
        if not token_data:
            return False, "Gmail not connected. Connect Gmail in Settings."

        # Build Credentials object
        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id="",  # Will use app config
            client_secret=""  # Will use app config
        )

        # Refresh if expired
        if token_data.get("expires_at") and token_data["expires_at"] < int(time.time()):
            try:
                from flask import current_app
                creds.client_id = current_app.config.get("GMAIL_CLIENT_ID")
                creds.client_secret = current_app.config.get("GMAIL_CLIENT_SECRET")
                creds.refresh(Request())
                # Save refreshed token
                from ..gmail_oauth import save_gmail_token
                save_gmail_token(tid, sender, creds.token, creds.refresh_token, 3600)
            except RefreshError:
                return False, "Gmail authentication expired. Please reconnect Gmail in Settings."
            except Exception as e:
                return False, f"Failed to refresh Gmail access: {str(e)}"

        # Build message
        business = get_setting(tid, "business_name", "Inventory Tracker")
        subject = f"Payment Reminder — {business}"

        msg_text = f"Subject: {subject}\nFrom: {sender}\nTo: {to_email}\n\n{message}"
        message_bytes = msg_text.encode("utf-8")
        message_b64 = base64.urlsafe_b64encode(message_bytes).decode("utf-8")

        try:
            service = build("gmail", "v1", credentials=creds)
            send_message = {"raw": message_b64}
            service.users().messages().send(userId="me", body=send_message).execute()
            return True, f"Email sent via Gmail to {to_email}"

        except HttpError as e:
            error_msg = str(e)
            if "401" in error_msg:
                return False, "Gmail authorization expired. Please reconnect Gmail in Settings."
            elif "403" in error_msg:
                return False, "Gmail permission denied. Please reconnect Gmail in Settings."
            else:
                return False, f"Gmail error: {error_msg[:100]}"

    except ImportError:
        return False, "Gmail API client not installed. Contact your administrator."
    except Exception as e:
        error_str = str(e)
        return False, f"Gmail error: {error_str[:100]}"


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
