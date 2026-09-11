"""Excel/API/URL synchronization service for real-time debtor updates."""

from . import safe_http as req_lib
import json
from datetime import datetime, date
from decimal import Decimal


def fetch_source_data(source_type, config):
    """Fetch data from URL, API, or other source.

    Args:
        source_type: "url" or "api"
        config: Dict with url/api_url, api_key, api_path, etc.

    Returns:
        Tuple of (headers, rows) or (None, None) if error
    """
    try:
        if source_type == "url":
            url = config.get("url", "").strip()
            if not url:
                return None, None

            resp = req_lib.get(url, timeout=15, headers={"User-Agent": "AutoStack-Sync/1.0"})
            resp.raise_for_status()

            # Parse as JSON or CSV
            if "json" in resp.headers.get("Content-Type", ""):
                data = resp.json()
                if isinstance(data, dict):
                    data = [data]
                return list(data[0].keys()) if data else [], data
            else:
                # CSV parsing
                import csv, io
                reader = csv.DictReader(io.StringIO(resp.text))
                headers = reader.fieldnames or []
                rows = list(reader)
                return headers, rows

        elif source_type == "api":
            api_url = config.get("api_url", "").strip()
            if not api_url:
                return None, None

            h = {"User-Agent": "AutoStack-Sync/1.0"}
            if config.get("api_key"):
                h["Authorization"] = f"Bearer {config.get('api_key')}"

            resp = req_lib.get(api_url, timeout=15, headers=h)
            resp.raise_for_status()

            data = resp.json()
            # Navigate to specified path if given
            if config.get("api_path"):
                for key in config.get("api_path", "").split("."):
                    if isinstance(data, dict):
                        data = data.get(key, data)

            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                return None, None

            return list(data[0].keys()) if data else [], data

        return None, None
    except Exception as e:
        print(f"Error fetching source data: {e}")
        return None, None


def parse_value(value, field_type="string"):
    """Parse and normalize a value from external source."""
    if value is None or value == "":
        return ""

    value = str(value).strip()

    if field_type == "amount":
        # Remove currency symbols and parse as float
        value = value.replace(",", "").lstrip("R$£€ ")
        try:
            return f"{float(value):.2f}"
        except:
            return "0.00"

    elif field_type == "date":
        # Try to parse common date formats
        for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]:
            try:
                parsed = datetime.strptime(value, fmt).date()
                return parsed.isoformat()
            except:
                pass
        return value  # Return as-is if can't parse

    elif field_type == "int":
        try:
            return str(int(value))
        except:
            return value

    return value


def calculate_balance(amount_owed, amount_paid):
    """Calculate remaining balance."""
    try:
        owed = float(str(amount_owed).replace(",", "").lstrip("R$£€") or 0)
        paid = float(str(amount_paid).replace(",", "").lstrip("R$£€") or 0)
        balance = owed - paid
        return max(0, balance)  # Don't go negative
    except:
        return 0


def calculate_status(balance, due_date, is_paid=False):
    """Calculate debtor status based on balance and due date."""
    if is_paid or balance <= 0:
        return "PAID"

    try:
        if due_date:
            due = datetime.strptime(due_date, "%Y-%m-%d").date()
            today = date.today()
            if today > due:
                return "OVERDUE"
            elif today == due:
                return "DUE_TODAY"
    except:
        pass

    return "CURRENT"


def detect_and_apply_changes(tid, source_type, config, mapping, unique_key_field,
                             external_source="api"):
    """
    Detect changes in external source and apply them to debtors.

    Args:
        tid: Tenant ID
        source_type: "url" or "api"
        config: Source config dict
        mapping: Column mapping dict
        unique_key_field: Column name to use as unique key (Invoice No., ID, etc.)
        external_source: Source identifier for audit log

    Returns:
        Dict with stats: {"added": N, "updated": N, "removed": N, "errors": [...]}
    """
    from ..tenant_db import (get_all_debtors, get_debtor, add_debtor,
                             update_debtor_from_sync, mark_debtor_removed_from_source,
                             calculate_next_reminder, set_default_reminder_mode)

    stats = {"added": 0, "updated": 0, "removed": 0, "errors": []}

    # Fetch fresh data from source
    headers, rows = fetch_source_data(source_type, config)
    if rows is None:
        stats["errors"].append(f"Failed to fetch from {source_type} source")
        return stats

    if not rows:
        stats["errors"].append("No data received from source")
        return stats

    # Get existing debtors with external keys
    existing_debtors = {}
    all_debtors = get_all_debtors(tid, show_paid=True)
    for d in all_debtors:
        key = d.get("external_key")
        if key:
            existing_debtors[key] = d

    # Track which external keys we saw in this sync
    seen_keys = set()

    # Process each row from external source
    for row in rows:
        try:
            # Extract unique key
            unique_col = mapping.get(unique_key_field, unique_key_field)
            external_key = row.get(unique_col, "").strip()

            if not external_key:
                continue  # Skip rows without unique key

            seen_keys.add(external_key)

            # Extract and normalize fields
            name = row.get(mapping.get("name", ""), "").strip()
            if not name:
                continue

            # Build update dict
            updates = {
                "name": name,
                "phone": parse_value(row.get(mapping.get("phone", ""), ""), "string"),
                "email": parse_value(row.get(mapping.get("email", ""), ""), "string"),
                "amount_owed": parse_value(row.get(mapping.get("amount_owed", ""), "0"), "amount"),
                "products_owed": row.get(mapping.get("products_owed", ""), "").strip(),
                "notes": row.get(mapping.get("notes", ""), "").strip(),
                "external_key": external_key,
                "external_source": external_source,
                "sync_status": "synced",
            }

            # Parse dates
            purchase_date = row.get(mapping.get("date_of_purchase", ""), "")
            if purchase_date:
                updates["date_of_purchase"] = parse_value(purchase_date, "date")

            due_date = row.get(mapping.get("due_date", ""), "")

            # Calculate balance if we have amount fields
            amount_owed = row.get(mapping.get("amount_owed", ""), "0")
            amount_paid = row.get(mapping.get("amount_paid", ""), "0")
            if amount_owed or amount_paid:
                balance = calculate_balance(amount_owed, amount_paid)
                updates["amount_owed"] = f"{balance:.2f}"

            # Check if this is a new or existing debtor
            if external_key in existing_debtors:
                # Update existing debtor
                debtor = existing_debtors[external_key]
                did = debtor["id"]

                # Don't overwrite custom reminders
                if debtor.get("reminder_mode") == "custom_interval":
                    pass  # Keep existing custom reminder
                elif debtor.get("reminder_mode") == "default":
                    # Recalculate default reminder if purchase date changed
                    if updates.get("date_of_purchase") != debtor.get("date_of_purchase"):
                        calculate_next_reminder(tid, did)

                if update_debtor_from_sync(tid, did, external_key, updates, external_source):
                    stats["updated"] += 1
            else:
                # Create new debtor
                updates["reminder_days"] = 14
                updates["notify_method"] = "email"

                try:
                    add_debtor(tid, **updates)
                    stats["added"] += 1
                except Exception as e:
                    stats["errors"].append(f"Failed to add {name}: {str(e)}")

        except Exception as e:
            stats["errors"].append(f"Error processing row: {str(e)}")

    # Mark debtors that are no longer in source as removed
    for ext_key, debtor in existing_debtors.items():
        if ext_key not in seen_keys and debtor.get("sync_status") == "synced":
            mark_debtor_removed_from_source(tid, debtor["id"], ext_key, external_source)
            stats["removed"] += 1

    return stats
