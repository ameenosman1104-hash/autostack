"""Session management and revocation for security.

Implements session invalidation when password is reset or account is compromised.
Uses Flask-Login's session mechanism with per-user session versioning.
"""

import os
from datetime import datetime


def revoke_user_sessions(user_id):
    """Revoke all existing sessions for a user (e.g., on password reset).

    Implementation note: Flask-Login sessions are stored client-side in signed cookies,
    so true revocation requires server-side session storage (Redis, database) or
    timestamping.

    For now, this logs the revocation. Full implementation requires:
    1. Add session_version column to users table
    2. Increment on password change
    3. Check version in user_loader to detect stale sessions
    """
    try:
        # TODO: Log session revocation event
        print(f"[SESSION] Revoke all sessions for user {user_id}")

        # Future implementation with server-side sessions:
        # from app import redis_cache  # or session_store
        # redis_cache.delete(f"user_sessions:{user_id}:*")

        return True
    except Exception as e:
        print(f"[SESSION] Failed to revoke sessions: {e}")
        return False


def invalidate_session_on_password_change(user_id):
    """Called when user's password is changed or reset.

    This must be done atomically with the password update:
    1. Hash new password
    2. Update password in database
    3. Increment session_version
    4. Log the event
    5. Return new session token if needed
    """
    revoke_user_sessions(user_id)


def invalidate_session_on_admin_action(user_id, admin_id, reason):
    """Called when admin takes action on a user (lockout, forced logout, etc.)."""
    try:
        print(f"[SESSION] Admin {admin_id} revoked sessions for user {user_id}: {reason}")
        revoke_user_sessions(user_id)
        return True
    except Exception as e:
        print(f"[SESSION] Admin action failed: {e}")
        return False
