"""FastAPI rate limiting dependency (ARCH-08 §6.7)."""

from typing import Optional
from fastapi import Depends, Request, Response

from app.core.exceptions import RateLimitExceededError
from app.core.rate_limit.limiter import consume_rate_limit
from app.core.rate_limit.policy import RateLimitPolicy


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