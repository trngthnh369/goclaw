"""Public-URL check for screenshot scenes.

The Chrome sidecar sits on the internal Docker network next to the gateway,
Postgres and the LLM proxy. A screenshot URL comes from a model, so it must never
point there: a picture of an internal admin page would end up in a public video.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {"localhost", "chrome", "goclaw", "postgres", "cliproxy", "metadata.google.internal"}


def check_public_url(url: str) -> str | None:
    """Return None if the URL is a public http(s) address, else the reason it is not."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "only http(s) URLs can be captured"
    host = (parsed.hostname or "").lower()
    if not host:
        return "URL has no host"
    if host in BLOCKED_HOSTS or "." not in host or host.endswith((".local", ".internal", ".lan")):
        return f"host {host!r} is not a public internet name"
    if parsed.username or parsed.password:
        return "URLs with credentials are not allowed"
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        return f"cannot resolve {host}: {exc}"
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified
                or address in ipaddress.ip_network("100.64.0.0/10")):
            return f"{host} resolves to a non-public address ({address})"
    return None
