"""Trusted client IP resolution (ARCH-08 §B.5, A.3.4; ARCH-19 §3.4).

THE ONLY MODULE IN app/ THAT PARSES X-Forwarded-For.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Optional

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger("app.core.client_ip")

MAX_IP_LENGTH = 45

TRUSTED = "TRUSTED"
NO_HEADER = "NO_HEADER"
HOPS_DISABLED = "HOPS_DISABLED"
CHAIN_TOO_SHORT = "CHAIN_TOO_SHORT"
INVALID_ADDRESS = "INVALID_ADDRESS"

PARSE_OUTCOMES: frozenset[str] = frozenset(
    {TRUSTED, NO_HEADER, HOPS_DISABLED, CHAIN_TOO_SHORT, INVALID_ADDRESS}
)


def normalise_ip(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None

    candidate = raw.strip()
    if not candidate:
        return None

    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing == -1:
            return None
        candidate = candidate[1:closing]
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]

    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None

    text = str(address)
    return text[:MAX_IP_LENGTH]


def parse_forwarded_for(
    forwarded_for: Optional[str],
    *,
    hops: int,
) -> tuple[Optional[str], str]:
    if hops <= 0:
        return None, HOPS_DISABLED

    if not forwarded_for:
        return None, NO_HEADER

    chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]

    if len(chain) < hops:
        return None, CHAIN_TOO_SHORT

    address = normalise_ip(chain[-hops])
    if address is None:
        return None, INVALID_ADDRESS

    return address, TRUSTED


def _configured_hops() -> int:
    try:
        return int(getattr(settings, "TRUSTED_PROXY_HOPS", 0) or 0)
    except (TypeError, ValueError):
        return 0


def resolve(
    *,
    socket_ip: Optional[str],
    forwarded_for: Optional[str],
    hops: Optional[int] = None,
    strict: bool = True,
) -> Optional[str]:
    resolved_hops = _configured_hops() if hops is None else int(hops)

    address, outcome = parse_forwarded_for(forwarded_for, hops=resolved_hops)

    if outcome == TRUSTED:
        return address

    if outcome == HOPS_DISABLED:
        return normalise_ip(socket_ip)

    if outcome in (CHAIN_TOO_SHORT, INVALID_ADDRESS):
        logger.warning(
            "client_ip.untrusted_chain",
            extra={
                "outcome": outcome,
                "trusted_proxy_hops": resolved_hops,
                "chain_length": len(
                    [p for p in (forwarded_for or "").split(",") if p.strip()]
                ),
            },
        )
        return None if strict else normalise_ip(socket_ip)

    return None if strict else normalise_ip(socket_ip)


def _socket_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host or None


def _forwarded_header(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    headers = getattr(request, "headers", None) or {}
    try:
        return headers.get("x-forwarded-for")
    except Exception:  # noqa: BLE001
        return None


def client_ip(request: Optional[Request]) -> Optional[str]:
    resolved = resolve(
        socket_ip=_socket_ip(request),
        forwarded_for=_forwarded_header(request),
        strict=False,
    )
    if resolved:
        return resolved

    peer = normalise_ip(_socket_ip(request))
    if peer:
        return peer

    raw = _socket_ip(request)
    return raw[:MAX_IP_LENGTH] if raw else "unknown"


def trusted_client_ip(request: Optional[Request]) -> Optional[str]:
    return resolve(
        socket_ip=_socket_ip(request),
        forwarded_for=_forwarded_header(request),
        strict=True,
    )


__all__ = [
    "CHAIN_TOO_SHORT",
    "HOPS_DISABLED",
    "INVALID_ADDRESS",
    "MAX_IP_LENGTH",
    "NO_HEADER",
    "PARSE_OUTCOMES",
    "TRUSTED",
    "client_ip",
    "normalise_ip",
    "parse_forwarded_for",
    "resolve",
    "trusted_client_ip",
]
