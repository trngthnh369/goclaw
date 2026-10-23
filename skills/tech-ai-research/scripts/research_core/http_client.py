from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit


class SourcePolicyError(RuntimeError):
    """Raised when a source URL violates the fixed allowlist policy."""


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: int


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip_address: str, timeout: int, context: ssl.SSLContext) -> None:
        super().__init__(host, 443, timeout=timeout, context=context)
        self._pinned_ip = ip_address
        self._pinned_context = context

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._pinned_context.wrap_socket(sock, server_hostname=self.host)


class HttpClient:
    def __init__(self, allowed_hosts: set[str], timeout_seconds: int = 20, max_bytes: int = 2_000_000, max_redirects: int = 3) -> None:
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._ssl_context = ssl.create_default_context()

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        current_url = url
        start = time.monotonic()
        request_headers = {"User-Agent": "GoClaw-TechAIResearch/0.1", **(headers or {})}
        for _ in range(self.max_redirects + 1):
            host, path, pinned_ip = self._validate_url(current_url)
            conn = PinnedHTTPSConnection(host, pinned_ip, self.timeout_seconds, self._ssl_context)
            try:
                conn.request("GET", path, headers={"Host": host, **request_headers})
                resp = conn.getresponse()
                status = resp.status
                resp_headers = {key.lower(): value for key, value in resp.getheaders()}
                if status in {301, 302, 303, 307, 308}:
                    location = resp_headers.get("location")
                    resp.read(8192)
                    if not location:
                        raise RuntimeError(f"HTTP {status} without Location for {current_url}")
                    next_url = urljoin(current_url, location)
                    self._validate_url(next_url)
                    current_url = next_url
                    continue
                body = resp.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise SourcePolicyError(f"response too large for {current_url}")
                elapsed = int((time.monotonic() - start) * 1000)
                return HttpResponse(
                    url=current_url,
                    status=status,
                    headers=resp_headers,
                    body=body,
                    elapsed_ms=elapsed,
                )
            finally:
                conn.close()
        raise SourcePolicyError(f"too many redirects for {url}")

    def fetch_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        resp = self.fetch(url, headers=headers)
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return json.loads(resp.body.decode("utf-8"))

    def _validate_url(self, url: str) -> tuple[str, str, str]:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise SourcePolicyError(f"non-https URL rejected: {url}")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise SourcePolicyError(f"host not allowlisted: {host}")
        if parts.port not in (None, 443):
            raise SourcePolicyError(f"port not allowed for {url}")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return host, path, self._resolve_global_ip(host)

    def _resolve_global_ip(self, host: str) -> str:
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SourcePolicyError(f"cannot resolve host {host}: {exc}") from exc
        for info in infos:
            ip_text = str(info[4][0])
            ip = ipaddress.ip_address(ip_text)
            if ip.is_global:
                return ip_text
        raise SourcePolicyError(f"host {host} did not resolve to a global address")
