"""Simple rate limiting for login attempts."""
import time
from collections import defaultdict
from functools import wraps
from flask import request, jsonify, abort


# Store login attempts: {ip: [(timestamp, success), ...]}
# Keyed by IP address, stores list of (timestamp, was_successful) tuples
_login_attempts = defaultdict(list)

# Limits: max 5 failed attempts per 15 minutes per IP
FAILED_LIMIT = 5
FAILED_WINDOW = 900  # 15 minutes


def get_client_ip():
    """Get client IP, handling proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def check_login_rate_limit():
    """Check if current client should be rate-limited for login.

    Raises 429 Too Many Requests if limit exceeded.
    """
    ip = get_client_ip()
    now = time.time()

    # Clean old attempts
    _login_attempts[ip] = [
        (ts, success) for ts, success in _login_attempts[ip]
        if now - ts < FAILED_WINDOW
    ]

    # Count failed attempts in window
    failed = sum(1 for ts, success in _login_attempts[ip] if not success)

    if failed >= FAILED_LIMIT:
        # Too many failures - reject
        abort(429)


def record_login_attempt(success):
    """Record a login attempt result (True if successful, False if failed)."""
    ip = get_client_ip()
    now = time.time()

    _login_attempts[ip].append((now, success))

    # Clean very old entries
    _login_attempts[ip] = [
        (ts, success) for ts, success in _login_attempts[ip]
        if now - ts < FAILED_WINDOW * 2
    ]


def rate_limit_login(f):
    """Decorator to apply rate limiting to login routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        check_login_rate_limit()
        return f(*args, **kwargs)
    return decorated
