"""Security utilities for SSRF prevention and URL validation."""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _ip_is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for network in _PRIVATE_NETWORKS:
        if ip in network:
            return True
    return False


def _resolved_hostname_is_private(hostname: str) -> bool:
    """Resolve a hostname and return True if any address is private."""
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        if _ip_is_private(ip_str):
            return True
    return False


def is_internal_url(url: str) -> bool:
    """Return True if the URL points to an internal/private address."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return True
        if hostname.lower() in _BLOCKED_HOSTNAMES:
            return True
        if _ip_is_private(hostname):
            return True
        # Hostname is not a literal IP; resolve via DNS and check addresses
        # to block DNS-rebinding attacks.
        if _resolved_hostname_is_private(hostname):
            return True
        return False
    except Exception:
        logger.exception("SSRF check failed for URL %r", url)
        return True


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Validate a webhook URL. Returns (ok, error_message)."""
    if not url.startswith(("http://", "https://")):
        return False, "URL must use http or https"
    if is_internal_url(url):
        return False, "Webhook URL must not point to internal addresses"
    return True, ""


def validate_endpoint_path(endpoint: str) -> tuple[bool, str]:
    """Validate a proxy endpoint path. Returns (ok, error_message)."""
    if ".." in endpoint:
        return False, "Invalid endpoint path"
    for segment in endpoint.split("/"):
        if segment and not all(c.isalnum() or c in "-_" for c in segment):
            return False, "Invalid endpoint path segment"
    return True, ""
