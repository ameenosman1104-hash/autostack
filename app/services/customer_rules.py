"""Customer identity rules shared by Job Cards and the customer API.

A phone number identifies a customer. Numbers are compared by their digits, so '082 123 4567' and
'0821234567' are the same customer. Names are not unique: two people can share a name.
"""
import re

PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,50}$")
MIN_PHONE_DIGITS = 7
MAX_NAME = 200


class CustomerError(ValueError):
    """Validation or duplicate error that is safe to show to the user.

    For a duplicate phone number, `existing` holds the customer who already has it, so the screen can offer to select them.
    """

    def __init__(self, message, existing=None):
        super().__init__(message)
        self.existing = existing


def phone_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def phone_key(value):
    """The digits used to compare numbers. A South African +27 number is the same as its 0-prefixed form."""
    digits = phone_digits(value)
    if digits.startswith("27") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits


def validate_new_customer(name, phone):
    name = str(name or "").strip()
    phone = str(phone or "").strip()
    if not name:
        raise CustomerError("Enter the customer's name")
    if len(name) > MAX_NAME:
        raise CustomerError(f"Customer name is too long (maximum {MAX_NAME} characters)")
    if not PHONE_RE.match(phone) or len(phone_digits(phone)) < MIN_PHONE_DIGITS:
        raise CustomerError("Enter a valid phone number (at least 7 digits)")
    return name, phone


def find_customer_by_phone(conn, phone, exclude_id=None):
    """Return the existing customer with this phone number in this tenant's database, or None."""
    digits = phone_key(phone)
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    for row in conn.execute("SELECT id, name, phone FROM customers WHERE phone <> ''").fetchall():
        if row["id"] != exclude_id and phone_key(row["phone"]) == digits:
            return dict(row)
    return None


def refuse_duplicate_phone(conn, phone, exclude_id=None):
    existing = find_customer_by_phone(conn, phone, exclude_id)
    if existing:
        raise CustomerError("This phone number already belongs to a customer. Select them instead of creating a duplicate.",
                            existing={"id": existing["id"], "name": existing["name"], "phone": existing["phone"]})
