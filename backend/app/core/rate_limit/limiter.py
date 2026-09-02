"""Rate limit decision engine (ARCH-08 §B.5, §6.4, §6.5, §11.2)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional
from fastapi import Request

from app.core.api_key_secret import parse_api_key_token
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

_backend: Optional[RateLimitBackend] = None
_backend_lock = threading.Lock()


@dataclass(frozen=True)
class RateLimitIdentity:
    dimension: str  # "kid" | "uid" | "ip"
    value: str


def resolve_identity(request: Request) -> RateLimitIdentity:
    api_key_id = getattr(request.state, "api_key_id", None) if request else None
    if api_key_id is not None:
        return RateLimitIdentity("kid", str(api_key_id))

    if request is not None:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token.startswith(("fp_live_", "fp_test_")):
                parsed = parse_api_key_token(token)
                if parsed is not None:
                    return RateLimitIdentity("kid", str(parsed.key_id))

    user_id = getattr(request.state, "user_id", None) if request else None
    if user_id is not None:
        return RateLimitIdentity("uid", str(user_id))

    ip = client_ip(request) or "unknown"
    return RateLimitIdentity("ip", ip)


def get_rate_limit_backend() -> Optional[RateLimitBackend]:
    global _backend
    if not settings.RATE_LIMIT_ENABLED:
        return None
    if _backend is not None:
        return _backend

    with _backend_lock:
        if _backend is not None:
            return _backend
        if settings.RATE_LIMIT_BACKEND == "memory":
            _backend = InMemoryBackend()
        else:
            client = get_redis_client()
            if client is None:
                return None
            _backend = RedisBackend(client)
        return _backend


def reset_rate_limit_backend() -> None:
    global _backend
    with _backend_lock:
        _backend = None


def consume_rate_limit(
    request: Request, policy: RateLimitPolicy
) -> RateLimitDecision:
    # Bypass rate limits during automated test runs
    if not settings.RATE_LIMIT_ENABLED or settings.ENVIRONMENT == "test":
        return RateLimitDecision(allowed=True, remaining=policy.limit, reset_seconds=0)

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
