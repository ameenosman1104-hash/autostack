"""Validation shared by debtor payment and reminder operations."""
from decimal import Decimal, InvalidOperation

def reminder_interval(value):
    try:
        days = int(str(value))
    except (ValueError, TypeError):
        raise ValueError("Invalid reminder interval")
    if not 1 <= days <= 365:
        raise ValueError("Reminder interval must be between 1 and 365 days")
    return days

def payment_amount(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid payment amount")
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
        raise ValueError("Payment must be a finite positive amount")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Payment may have at most two decimal places")
    return float(amount)
