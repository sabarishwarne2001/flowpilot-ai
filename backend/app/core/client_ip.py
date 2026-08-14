"""Trusted client IP resolution (ARCH-08 §B.5, A.3.4).

THE ONLY MODULE IN app/ THAT READS X-Forwarded-For.

X-Forwarded-For is an append-only list that each hop extends. The LEFTMOST
entry is whatever the original client wrote, so it is attacker-controlled and
must never be trusted. The trustworthy portion is the RIGHTMOST
TRUSTED_PROXY_HOPS entries.
"""

from __future__ import annotations

from typing import Optional
from fastapi import Request

from app.core.config import settings


def client_ip(request: Request) -> Optional[str]:
    hops = settings.TRUSTED_PROXY_HOPS
    if hops > 0 and request is not None:
        headers = getattr(request, "headers", {}) or {}
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            chain = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(chain) >= hops:
                return chain[-hops][:45]

    if request is not None:
        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return client.host[:45]

    return "unknown"