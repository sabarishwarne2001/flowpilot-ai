"""ARCH-09 §B.6 — the SSRF-safe HTTP client, standalone, before anything calls it."""

from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS: float = 10.0
TOTAL_TIMEOUT_SECONDS: float = 30.0
DNS_TIMEOUT_SECONDS: float = 5.0
MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024
READ_CHUNK_BYTES: int = 64 * 1024

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

_dns_executor = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="ssrf-client-dns"
)


class SSRFClientError(Exception):
    """Base class. Message must never include request or response bodies."""


class InvalidURLError(SSRFClientError):
    pass


class DNSResolutionError(SSRFClientError):
    pass


class ForbiddenAddressError(SSRFClientError):
    """A candidate address resolved into a disallowed range."""


class ConnectError(SSRFClientError):
    pass


class TLSError(SSRFClientError):
    pass


class TimeoutExceededError(SSRFClientError):
    pass


class ResponseTooLargeError(SSRFClientError):
    pass


@dataclass(frozen=True, slots=True)
class SSRFResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    resolved_ip: str
    elapsed_seconds: float


def _is_forbidden_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> Optional[str]:
    if ip == _METADATA_IP:
        return "cloud metadata address"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_unspecified:
        return "unspecified"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK:
        return "carrier-grade NAT (RFC 6598)"
    if ip.is_private:
        return "private"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.is_site_local:
            return "IPv6 site-local"
        if ip.ipv4_mapped is not None:
            mapped_reason = _is_forbidden_ip(ip.ipv4_mapped)
            if mapped_reason:
                return f"IPv4-mapped IPv6 ({mapped_reason})"
    return None


def _resolve(hostname: str, port: int, *, deadline: float) -> list[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutExceededError("Timed out before DNS resolution began.")
    budget = min(remaining, DNS_TIMEOUT_SECONDS)

    future = _dns_executor.submit(
        socket.getaddrinfo, hostname, port, proto=socket.IPPROTO_TCP
    )
    try:
        infos = future.result(timeout=budget)
    except FutureTimeoutError as exc:
        raise DNSResolutionError(
            f"DNS resolution for '{hostname}' exceeded {budget:.1f}s."
        ) from exc
    except socket.gaierror as exc:
        raise DNSResolutionError(f"DNS resolution for '{hostname}' failed: {exc}") from exc

    candidates: list[str] = []
    forbidden: list[tuple[str, str]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        raw_ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        reason = _is_forbidden_ip(ip_obj)
        if reason:
            forbidden.append((raw_ip, reason))
        else:
            candidates.append(raw_ip)

    if forbidden:
        details = ", ".join(f"{ip} ({why})" for ip, why in forbidden)
        raise ForbiddenAddressError(
            f"'{hostname}' resolved to a disallowed address: {details}"
        )
    if not candidates:
        raise DNSResolutionError(f"'{hostname}' resolved to no usable address.")
    return candidates


def resolve_and_validate(hostname: str, port: int = 443, *, timeout: float = 5.0) -> list[str]:
    return _resolve(hostname, port, deadline=time.monotonic() + timeout)


class SSRFSafeHTTPClient:
    def __init__(
        self,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        total_timeout: float = TOTAL_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        allow_private_ranges: bool = False,
        test_ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        self._connect_timeout = connect_timeout
        self._total_timeout = total_timeout
        self._max_response_bytes = max_response_bytes
        self._allow_private_ranges = allow_private_ranges
        self._test_ssl_context = test_ssl_context

        if test_ssl_context is not None and not allow_private_ranges:
            raise RuntimeError(
                "test_ssl_context supplied without allow_private_ranges=True."
            )
        if allow_private_ranges:
            try:
                from app.core.config import settings

                if getattr(settings, "ENVIRONMENT", "development") == "production":
                    raise RuntimeError(
                        "allow_private_ranges=True in a production environment."
                    )
            except ImportError:
                pass

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
    ) -> SSRFResponse:
        started = time.monotonic()
        deadline = started + self._total_timeout

        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise InvalidURLError(
                f"Only https:// URLs are permitted, got scheme '{parsed.scheme}'."
            )
        hostname = parsed.hostname
        if not hostname:
            raise InvalidURLError("URL has no hostname.")
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        if self._allow_private_ranges:
            candidates = self._resolve_unchecked(hostname, port)
        else:
            candidates = _resolve(hostname, port, deadline=deadline)

        last_error: Optional[Exception] = None
        for ip in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutExceededError(
                    f"Total timeout of {self._total_timeout:.0f}s exceeded."
                )
            try:
                return self._send(
                    method=method,
                    hostname=hostname,
                    ip=ip,
                    port=port,
                    path=path,
                    headers=headers or {},
                    body=body,
                    deadline=deadline,
                    started=started,
                )
            except (ConnectError, TLSError) as exc:
                last_error = exc
                logger.warning(
                    "ssrf_client.connect_failed",
                    extra={"hostname": hostname, "ip": ip, "error": str(exc)},
                )
                continue

        assert last_error is not None
        raise last_error

    def _resolve_unchecked(self, hostname: str, port: int) -> list[str]:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        seen: list[str] = []
        for _f, _t, _p, _c, sockaddr in infos:
            ip = sockaddr[0]
            if ip not in seen:
                seen.append(ip)
        if not seen:
            raise DNSResolutionError(f"'{hostname}' resolved to no usable address.")
        return seen

    def _send(
        self,
        *,
        method: str,
        hostname: str,
        ip: str,
        port: int,
        path: str,
        headers: dict[str, str],
        body: bytes,
        deadline: float,
        started: float,
    ) -> SSRFResponse:
        remaining_connect = min(self._connect_timeout, max(deadline - time.monotonic(), 0.1))

        raw_sock = socket.create_connection((ip, port), timeout=remaining_connect)
        try:
            context = self._test_ssl_context or ssl.create_default_context()
            tls_sock = context.wrap_socket(raw_sock, server_hostname=hostname)
        except ssl.SSLError as exc:
            raw_sock.close()
            raise TLSError(f"TLS handshake with '{hostname}' failed: {exc}") from exc
        except OSError as exc:
            raw_sock.close()
            raise ConnectError(f"Connection to {ip}:{port} failed: {exc}") from exc

        conn = _PinnedHTTPSConnection(hostname, port, timeout=remaining_connect)
        conn.sock = tls_sock

        remaining_total = max(deadline - time.monotonic(), 0.1)
        tls_sock.settimeout(remaining_total)

        try:
            conn.request(method, path, body=body, headers=headers)
            raw_response = conn.getresponse()

            chunks: list[bytes] = []
            total = 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutExceededError("Total timeout exceeded reading body.")
                chunk = raw_response.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > self._max_response_bytes:
                    raise ResponseTooLargeError("Response exceeded byte ceiling.")
                chunks.append(chunk)

            response_headers = {k.lower(): v for k, v in raw_response.getheaders()}
            return SSRFResponse(
                status_code=raw_response.status,
                headers=response_headers,
                body=b"".join(chunks),
                resolved_ip=ip,
                elapsed_seconds=time.monotonic() - started,
            )
        except socket.timeout as exc:
            raise TimeoutExceededError(f"Socket timeout with {ip}:{port}.") from exc
        except OSError as exc:
            raise ConnectError(f"Communication with {ip}:{port} failed: {exc}") from exc
        finally:
            conn.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        if self.sock is None:
            raise ConnectError("_PinnedHTTPSConnection invoked with no socket.")
