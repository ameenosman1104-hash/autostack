"""Background reminder scheduler using persistent dispatch ledger.

Deduplicates across process restarts using reminder_dispatch table.
Skips paid, paused, and removed-from-source debtors.
Records all attempts for audit trail.
"""
import sqlite3
from datetime import datetime, date
from app.tenant_db import (get_conn, get_debtor, get_all_debtors, get_setting,
                           calculate_next_reminder, log_notification)
from app.services.debt_notifier import send_reminder


def check_and_send_reminders(tid):
    """Check all debtors in tenant and send due reminders.

    Returns dict with statistics: {sent: N, failed: N, skipped: N, errors: []}
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}

    try:
        # Check if reminders are enabled for this business
        if not get_setting(tid, "reminders_enabled", "0").strip() in ("1", "true", "yes"):
            stats["skipped"] = "Reminders disabled for this business"
            return stats

        # Get all debtors
        all_debtors = get_all_debtors(tid)

        for debtor in all_debtors:
            try:
                # Skip if paid
                if debtor.get("is_paid"):
                    continue

                # Skip if reminders paused
                paused_until = debtor.get("reminders_paused_until")
                if paused_until:
                    try:
                        until_date = date.fromisoformat(paused_until)
                        if until_date >= date.today():
                            continue
                    except:
                        pass

                # Skip if removed from sync source
                if debtor.get("sync_status") == "removed_from_source":
                    continue

                # Check if reminder is due
                next_reminder = calculate_next_reminder(tid, debtor["id"])
                if not next_reminder:
                    continue

                try:
                    next_date = date.fromisoformat(next_reminder)
                except:
                    continue

                if next_date > date.today():
                    # Not due yet
                    continue

                # Reminder is due - check dispatch ledger for deduplication
                conn = get_conn(tid)
                dispatch = conn.execute(
                    "SELECT id, status FROM reminder_dispatch WHERE debtor_id=? AND scheduled_date=?",
                    (debtor["id"], next_reminder)
                ).fetchone()
                conn.close()

                if dispatch:
                    # Already attempted today
                    status = dispatch[1]
                    if status == "sent":
                        # Already sent successfully
                        continue
                    elif status == "failed":
                        # Previously failed - don't auto-retry, needs review
                        continue
                    # pending - try sending

                # Send the reminder
                ok, msg = send_reminder(tid, debtor)

                # Record attempt in dispatch ledger
                conn = get_conn(tid)
                try:
                    if dispatch:
                        # Update existing record
                        conn.execute(
                            "UPDATE reminder_dispatch SET status=?, error_message=?, sent_at=? WHERE id=?",
                            ("sent" if ok else "failed", msg if not ok else None,
                             datetime.now().isoformat() if ok else None, dispatch[0])
                        )
                    else:
                        # Insert new record
                        conn.execute(
                            """INSERT INTO reminder_dispatch
                               (debtor_id, scheduled_date, status, error_message, sent_at)
                               VALUES (?, ?, ?, ?, ?)""",
                            (debtor["id"], next_reminder,
                             "sent" if ok else "failed",
                             msg if not ok else None,
                             datetime.now().isoformat() if ok else None)
                        )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    stats["errors"].append(f"Debtor {debtor['id']}: ledger update failed: {e}")

                finally:
                    conn.close()

                if ok:
                    stats["sent"] += 1
                else:
                    stats["failed"] += 1
                    stats["errors"].append(f"Debtor {debtor['id']}: {msg}")

            except Exception as e:
                stats["errors"].append(f"Debtor {debtor.get('id', '?')}: {e}")

    except Exception as e:
        stats["errors"].append(f"Scheduler error: {e}")

    return stats


def enable_reminders(tid):
    """Enable automatic reminders for this business."""
    from app.tenant_db import save_setting
    save_setting(tid, "reminders_enabled", "1")


def disable_reminders(tid):
    """Disable automatic reminders for this business."""
    from app.tenant_db import save_setting
    save_setting(tid, "reminders_enabled", "0")


def pause_debtor_reminders(tid, did, until_date):
    """Pause reminders for a specific debtor until a date (YYYY-MM-DD).

    Pass None to unpause.
    """
    from app.tenant_db import update_debtor
    update_debtor(tid, did, reminders_paused_until=until_date)


def get_dispatch_status(tid, did=None, limit=100):
    """Get reminder dispatch history for audit trail.

    If did is None, returns recent entries for entire tenant.
    """
    conn = get_conn(tid)
    if did:
        rows = conn.execute(
            """SELECT debtor_id, scheduled_date, status, error_message, sent_at
               FROM reminder_dispatch WHERE debtor_id=? ORDER BY created_at DESC LIMIT ?""",
            (did, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT debtor_id, scheduled_date, status, error_message, sent_at
               FROM reminder_dispatch ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(zip(["debtor_id", "scheduled_date", "status", "error_message", "sent_at"], r)) for r in rows]
