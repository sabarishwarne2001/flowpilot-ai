"""Global rate limit middleware (ARCH-08 §6.8).

Campaign session 3 (N-061). A request carrying a valid access token is counted against that
person (`POLICY_USER_DEFAULT`, RATE_LIMIT_USER_PER_MINUTE); anything else against the client
address (`POLICY_GLOBAL_IP`); API keys against the key (the limiter's own identity rule). Before,
this ran before sign-in was resolved, so every browser request was counted per address: a company
behind one office address shared one allowance, and one person on many addresses had none.
"""

from __future__ import annotations

import json
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.public_route_registry import policy_for
from app.core.rate_limit.limiter import consume_rate_limit
from app.core.rate_limit.policy import (
    POLICY_GLOBAL_IP,
    POLICY_USER_DEFAULT,
    RateLimitPolicy,
    resolve_policy,
)

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
        if policy_name:
            policy: RateLimitPolicy = resolve_policy(policy_name)
        elif _signed_in_subject(request) is not None:
            policy = POLICY_USER_DEFAULT
        else:
            policy = POLICY_GLOBAL_IP

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


def _signed_in_subject(request: Request) -> str | None:
    """The person a browser request is from, when it carries a valid access token.

    Signature and expiry only (no database): revocation is decided later, by sign-in itself.
    Recorded on the request for the limiter's identity rule.
    """
    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    if not token or token.startswith(("svb_live_", "svb_test_")):
        return None
    from app.core.security import decode_access_token_claims

    claims = decode_access_token_claims(token)
    if claims is None:
        return None
    subject = str(claims.subject)
    request.state.rate_limit_user_id = subject
    return subject
