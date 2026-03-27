"""Input validation helpers for security hardening."""

import ipaddress
import socket
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """Check that a URL is safe to request (not targeting loopback or link-local).

    Allows private LAN IPs (192.168.x.x, 10.x.x.x, 172.16-31.x.x) since users
    legitimately test connections to local AI servers (Ollama, ComfyUI, etc.).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ('http', 'https'):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for _, _, _, _, addr in resolved:
            ip = ipaddress.ip_address(addr[0])
            if ip.is_loopback or ip.is_link_local:
                return False
    except (socket.gaierror, ValueError):
        return False

    return True
