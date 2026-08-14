"""Rate limit decision engine (ARCH-08 §B.5, §6.4, §6.5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from fastapi import Request

from app.core.client_ip import client_ip
from app.core.config import settings
from app.core.rate_limit.backend import (
    InMemoryBackend,
    RateLimitBackend,
    RateLimitDecision,
    RedisBackend,
)
from app.core.rate_limit.policy import FailureMode, RateLimitPolicy
from app.core.redis_client import get_redis_client

logger = logging.getLogger("app.core.rate_limit.limiter")


@dataclass(frozen=True)
class RateLimitIdentity:
    dimension: str  # "kid" | "uid" | "ip"
    value: str


def resolve_identity(request: Request) -> RateLimitIdentity:
    api_key_id = getattr(request.state, "api_key_id", None) if request else None
    if api_key_id is not None:
        return RateLimitIdentity("kid", str(api_key_id))

    user_id = getattr(request.state, "user_id", None) if request else None
    if user_id is not None:
        return RateLimitIdentity("uid", str(user_id))

    ip = client_ip(request) or "unknown"
    return RateLimitIdentity("ip", ip)


def get_rate_limit_backend() -> Optional[RateLimitBackend]:
    if not settings.RATE_LIMIT_ENABLED:
        return None

    if settings.RATE_LIMIT_BACKEND == "memory":
        return InMemoryBackend()

    client = get_redis_client()
    if client is None:
        return None
    return RedisBackend(client)


def consume_rate_limit(
    request: Request, policy: RateLimitPolicy
) -> RateLimitDecision:
    backend = get_rate_limit_backend()
    if backend is None:
        if policy.failure_mode == FailureMode.FAIL_CLOSED:
            logger.error("Rate limiter backend unavailable for fail-closed policy %s", policy.name)
            return RateLimitDecision(allowed=False, remaining=0, reset_seconds=60)
        logger.warning("Rate limiter backend unavailable; failing open for policy %s", policy.name)
        return RateLimitDecision(allowed=True, remaining=policy.limit, reset_seconds=0)

    identity = resolve_identity(request)
    key = f"rl:v1:{policy.name}:{identity.dimension}:{identity.value}"

    try:
        return backend.consume(
            key=key, limit=policy.limit, window_seconds=policy.window_seconds
        )
    except Exception as exc:
        logger.exception("Error executing rate limit check for policy %s: %s", policy.name, exc)
        if policy.failure_mode == FailureMode.FAIL_CLOSED:
            return RateLimitDecision(allowed=False, remaining=0, reset_seconds=60)
        return RateLimitDecision(allowed=True, remaining=policy.limit, reset_seconds=0)