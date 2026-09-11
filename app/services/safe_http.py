"""Restrict import destinations and redirects to public HTTP services.
Deployment egress rules should additionally block private networks to prevent DNS rebinding.
"""
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin
import requests

def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Import URL must be a public HTTP or HTTPS URL without credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("Import URL must use port 80 or 443")
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Import URL cannot access private or local network addresses")
    return url

def get(url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    with requests.Session() as session:
        session.trust_env = False
        for attempt in range(6):
            validate_url(url)
            response = session.get(url, headers=headers, allow_redirects=False, **kwargs)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            target = urljoin(url, response.headers.get("Location", ""))
            response.close()
            if urlsplit(target).netloc != urlsplit(url).netloc:
                headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "cookie")}
            url = target
        raise ValueError("Too many import URL redirects")
