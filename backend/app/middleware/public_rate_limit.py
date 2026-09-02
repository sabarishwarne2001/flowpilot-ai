"""ARCH-21 §3.2 — IETF rate limit headers for the public gateway."""

from __future__ import annotations

import logging
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.middleware.public_rate_limit")

HEADER_LIMIT = "X-RateLimit-Limit"
HEADER_REMAINING = "X-RateLimit-Remaining"
HEADER_RESET = "X-RateLimit-Reset"

HEADER_LIMIT_IETF = "RateLimit-Limit"
HEADER_REMAINING_IETF = "RateLimit-Remaining"
HEADER_RESET_IETF = "RateLimit-Reset"

HEADER_TIER = "X-RateLimit-Tier"

RATE_LIMIT_HEADERS: tuple[str, ...] = (
    HEADER_LIMIT,
    HEADER_REMAINING,
    HEADER_RESET,
    HEADER_LIMIT_IETF,
    HEADER_REMAINING_IETF,
    HEADER_RESET_IETF,
    HEADER_TIER,
)

PUBLIC_API_PREFIX: str = "/api/v1/public"


def apply_rate_limit_headers(
    response: Response, snapshot: dict[str, Any]
) -> None:
    limit = str(int(snapshot.get("limit", 0)))
    remaining = str(max(0, int(snapshot.get("remaining", 0))))
    reset = str(int(snapshot.get("reset_seconds", 0)))

    response.headers[HEADER_LIMIT] = limit
    response.headers[HEADER_REMAINING] = remaining
    response.headers[HEADER_RESET] = reset
    response.headers[HEADER_LIMIT_IETF] = limit
    response.headers[HEADER_REMAINING_IETF] = remaining
    response.headers[HEADER_RESET_IETF] = reset

    tier = snapshot.get("tier")
    if tier:
        response.headers[HEADER_TIER] = str(tier)


class PublicApiRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        if not request.url.path.startswith(PUBLIC_API_PREFIX):
            return response

        snapshot: Optional[dict[str, Any]] = getattr(
            request.state, "public_rate_limit", None
        )
        if not snapshot:
            return response

        try:
            apply_rate_limit_headers(response, snapshot)
        except Exception:
            logger.warning(
                "public_api.rate_limit_headers_not_applied",
                exc_info=True,
            )

        return response


__all__ = [
    "HEADER_LIMIT",
    "HEADER_LIMIT_IETF",
    "HEADER_REMAINING",
    "HEADER_REMAINING_IETF",
    "HEADER_RESET",
    "HEADER_RESET_IETF",
    "HEADER_TIER",
    "PUBLIC_API_PREFIX",
    "RATE_LIMIT_HEADERS",
    "PublicApiRateLimitMiddleware",
    "apply_rate_limit_headers",
]
