"""Global per-IP rate limit middleware (ARCH-08 §6.8)."""

from __future__ import annotations

import json
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.public_route_registry import policy_for
from app.core.rate_limit.limiter import consume_rate_limit
from app.core.rate_limit.policy import POLICY_GLOBAL_IP, RateLimitPolicy, resolve_policy

EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/api/v1/health",
        "/docs",
        "/openapi.json",
    }
)


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Consult the public-route registry for special/public routes
        policy_name = policy_for(request.url.path, request.method)
        policy: RateLimitPolicy = (
            resolve_policy(policy_name) if policy_name else POLICY_GLOBAL_IP
        )

        decision = consume_rate_limit(request, policy)
        if not decision.allowed:
            headers = {
                "Retry-After": str(decision.reset_seconds),
                "RateLimit-Limit": str(policy.limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(decision.reset_seconds),
                "Content-Type": "application/json",
            }
            body = json.dumps(
                {
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Rate limit exceeded. Please retry shortly.",
                    }
                }
            ).encode("utf-8")
            return Response(
                content=body,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers=headers,
            )

        response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(policy.limit)
        response.headers["RateLimit-Remaining"] = str(max(decision.remaining, 0))
        response.headers["RateLimit-Reset"] = str(decision.reset_seconds)
        return response
