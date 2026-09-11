"""Rate limiting for login attempts, keyed by username (not IP).

Multi-tenant apps should rate-limit by USERNAME to prevent one user's
failed attempts from blocking all users behind the same proxy/load balancer.

On Google App Engine, all users appear as the same IP due to the load
balancer. Rate-limiting by IP would block the entire app after 5 failed
attempts by any user. Rate-limiting by username is more appropriate.
"""
import time
import logging
from collections import defaultdict
from flask import request, abort

logger = logging.getLogger(__name__)

# Store login attempts: {username: [(timestamp, success), ...]}
# Keyed by username, stores list of (timestamp, was_successful) tuples
_login_attempts = defaultdict(list)

# Limits: max 5 failed attempts per 15 minutes per username
FAILED_LIMIT = 5
FAILED_WINDOW = 900  # 15 minutes


def check_login_rate_limit(username):
    """Check if username should be rate-limited for login.

    Args:
        username: The username attempting to log in

    Raises 429 Too Many Requests if limit exceeded.
    """
    now = time.time()

    # Clean old attempts
    _login_attempts[username] = [
        (ts, success) for ts, success in _login_attempts[username]
        if now - ts < FAILED_WINDOW
    ]

    # Count failed attempts in window
    failed = sum(1 for ts, success in _login_attempts[username] if not success)

    if failed >= FAILED_LIMIT:
        logger.warning(f"Rate limit exceeded for login attempt on username: {username} (too many failed attempts)")
        abort(429)


def record_login_attempt(username, success):
    """Record a login attempt result.

    Args:
        username: The username that was attempted
        success: True if login succeeded, False if failed
    """
    now = time.time()

    _login_attempts[username].append((now, success))

    # Clean very old entries
    _login_attempts[username] = [
        (ts, success) for ts, success in _login_attempts[username]
        if now - ts < FAILED_WINDOW * 2
    ]

    if not success:
        logger.info(f"Failed login attempt on username: {username}")
    else:
        logger.info(f"Successful login for username: {username}")
