"""Technicians: who works on job cards, and what they have completed.

A technician is never deleted. Deactivating one hides them from new job cards, and their history stays.
A completed job is one that was collected (status 'completed'), not one that was merely assigned.
"""
import re

from ..tenant_db import get_conn
from .job_cards import JobCardError
from .workshop_history import _job_lines

NAME_MAX = 100
PHONE_MAX = 50
PHONE_RE = re.compile(r"^[0-9+()\-\s]{0,50}$")

_COUNTS = """SELECT t.*,
                 (SELECT COUNT(*) FROM job_cards j WHERE j.technician_id = t.id) AS assigned_jobs,
                 (SELECT COUNT(*) FROM job_cards j WHERE j.technician_id = t.id AND j.status = 'completed') AS completed_jobs
              FROM technicians t"""


def _clean(name, phone):
    name = str(name or "").strip()
    phone = str(phone or "").strip()
    if not name:
        raise JobCardError("Enter the technician's name")
    if len(name) > NAME_MAX:
        raise JobCardError(f"Technician name is too long (maximum {NAME_MAX} characters)")
    if len(phone) > PHONE_MAX or (phone and not PHONE_RE.match(phone)):
        raise JobCardError("Enter a valid phone number or leave it blank")
    return name, phone


def _name_taken(conn, name, exclude_id=None):
    row = conn.execute("SELECT id FROM technicians WHERE lower(trim(name)) = lower(?) AND id IS NOT ?",
                       (name, exclude_id)).fetchone()
    return row is not None


def get_technician(tid, technician_id):
    conn = get_conn(tid)
    try:
        row = conn.execute(_COUNTS + " WHERE t.id = ?", (int(technician_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_technicians(tid, active_only=False):
    conn = get_conn(tid)
    try:
        sql = _COUNTS + (" WHERE t.is_active = 1" if active_only else "") + " ORDER BY t.is_active DESC, t.name"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def create_technician(tid, name, phone="", actor=""):
    name, phone = _clean(name, phone)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _name_taken(conn, name):
            raise JobCardError("A technician with this name already exists")
        cur = conn.execute("INSERT INTO technicians (name, phone, is_active) VALUES (?,?,1)", (name, phone))
        conn.commit()
        technician_id = cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_technician(tid, technician_id)


def update_technician(tid, technician_id, name, phone, is_active):
    name, phone = _clean(name, phone)
    conn = get_conn(tid)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM technicians WHERE id=?", (int(technician_id),)).fetchone():
            raise JobCardError("Technician not found")
        if _name_taken(conn, name, exclude_id=int(technician_id)):
            raise JobCardError("A technician with this name already exists")
        conn.execute("""UPDATE technicians SET name=?, phone=?, is_active=?, updated_at=datetime('now') WHERE id=?""",
                     (name, phone, 1 if is_active else 0, int(technician_id)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_technician(tid, technician_id)


def technician_history(tid, technician_id):
    """The technician, and every job card they are assigned to, newest first, with services performed."""
    technician = get_technician(tid, technician_id)
    if technician is None:
        return None
    conn = get_conn(tid)
    try:
        rows = conn.execute("""SELECT j.id, j.job_number, j.status, j.customer_name, j.opened_at, j.completed_at,
                                      v.registration
                               FROM job_cards j JOIN vehicles v ON v.id = j.vehicle_id
                               WHERE j.technician_id = ? ORDER BY j.id DESC""", (int(technician_id),)).fetchall()
        jobs = [dict(r) for r in rows]
        lines = _job_lines(conn, [j["id"] for j in jobs])
        for job in jobs:
            job["services"] = lines[job["id"]]["services"]
            job["tyres"] = lines[job["id"]]["tyres"]
        return {"technician": technician, "jobs": jobs}
    finally:
        conn.close()
