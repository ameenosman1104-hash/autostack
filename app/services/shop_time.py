"""Shop time for financial records.

Each new transaction stores two things:
- recorded_at_utc: the exact instant it was recorded, in UTC.
- business_date: the calendar date it belongs to, in the shop's timezone.

The shop's timezone is Africa/Johannesburg unless a tenant setting says otherwise. Historical rows are never
rewritten; they keep whatever they already have.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_SHOP_TIMEZONE = "Africa/Johannesburg"
SHOP_TIMEZONE_SETTING = "shop_timezone"


def zone(name=None):
    return ZoneInfo(name or DEFAULT_SHOP_TIMEZONE)


def utc_now():
    return datetime.now(timezone.utc)


def utc_timestamp(moment=None):
    """A UTC timestamp as 'YYYY-MM-DD HH:MM:SS', the same format SQLite uses elsewhere in the database."""
    return (moment or utc_now()).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def business_date_for(moment=None, tz_name=None):
    """The shop's calendar date for an instant. Uses the given timezone name, or the default."""
    return (moment or utc_now()).astimezone(zone(tz_name)).date().isoformat()


def shop_timezone_name(conn):
    """The tenant's configured timezone, read on the caller's connection so it sits in the same transaction."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SHOP_TIMEZONE_SETTING,)).fetchone()
    name = row[0] if row and row[0] else DEFAULT_SHOP_TIMEZONE
    zone(name)  # raises if the name is not a valid timezone
    return name


def today_business_date(conn):
    return business_date_for(utc_now(), shop_timezone_name(conn))


def is_iso_date(value):
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False
