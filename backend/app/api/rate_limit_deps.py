"""FastAPI rate limiting dependencies (ARCH-08 §6.7, §11.4)."""

from typing import Optional
from fastapi import Depends, Request, Response

from app.core.exceptions import RateLimitExceededError
from app.core.rate_limit.limiter import consume_rate_limit
from app.core.rate_limit.policy import POLICY_API_KEY_DEFAULT, RateLimitPolicy


class RateLimiter:
    def __init__(self, policy: RateLimitPolicy) -> None:
        self.policy = policy

    def __call__(self, request: Request, response: Response) -> None:
        decision = consume_rate_limit(request, self.policy)

        response.headers["RateLimit-Limit"] = str(self.policy.limit)
        response.headers["RateLimit-Remaining"] = str(max(decision.remaining, 0))
        response.headers["RateLimit-Reset"] = str(decision.reset_seconds)

        if not decision.allowed:
            raise RateLimitExceededError(
                "Too many requests. Please retry shortly.",
                retry_after=decision.reset_seconds,
                policy=self.policy.name,
            )


class ApiKeyRateLimiter:
    """Per-key rate limiter for API key authenticated requests."""

    def __init__(self, policy: RateLimitPolicy = POLICY_API_KEY_DEFAULT) -> None:
        self.policy = policy

    def __call__(self, request: Request, response: Response) -> None:
        api_key_id = getattr(request.state, "api_key_id", None) if request else None
        if api_key_id is None:
            return

        decision = consume_rate_limit(request, self.policy)

        response.headers["RateLimit-Limit"] = str(self.policy.limit)
        response.headers["RateLimit-Remaining"] = str(max(decision.remaining, 0))
        response.headers["RateLimit-Reset"] = str(decision.reset_seconds)

        if not decision.allowed:
            raise RateLimitExceededError(
                "API key rate limit exceeded.",
                retry_after=decision.reset_seconds,
                policy=self.policy.name,
            )
