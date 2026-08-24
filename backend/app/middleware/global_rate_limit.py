"""Global per-IP rate limit middleware (ARCH-08 §6.8)."""

from __future__ import annotations

import json
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.client_ip import client_ip
from app.core.rate_limit.limiter import consume_rate_limit
from app.core.rate_limit.policy import POLICY_GLOBAL_IP

EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/api/v1/health",
        "/docs",
        "/openapi.json",
        # ARCH-15 Step 15.1. Stripe delivers from a small set of addresses and
        # bursts hard after resolving an outage of its own. A per-IP limit
        # would shed exactly that recovery burst — dropping billing events
        # for a reason that looks like protection. The endpoint's own
        # defences are the signature check and the body-size bound, both of
        # which run before any work.
        "/api/v1/billing/stripe/webhook",
    }
)


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        decision = consume_rate_limit(request, POLICY_GLOBAL_IP)
        if not decision.allowed:
            headers = {
                "Retry-After": str(decision.reset_seconds),
                "RateLimit-Limit": str(POLICY_GLOBAL_IP.limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(decision.reset_seconds),
                "Content-Type": "application/json",
            }
            body = json.dumps(
                {
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Global rate limit exceeded. Please retry shortly.",
                    }
                }
            ).encode("utf-8")
            return Response(
                content=body,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers=headers,
            )

        response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(POLICY_GLOBAL_IP.limit)
        response.headers["RateLimit-Remaining"] = str(max(decision.remaining, 0))
        response.headers["RateLimit-Reset"] = str(decision.reset_seconds)
        return response