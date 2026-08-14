"""
Redis client connection factory for FlowPilot AI (ARCH-08 §B.5).
"""

from __future__ import annotations

import logging
from typing import Optional
import redis

from app.core.config import settings

logger = logging.getLogger("app.core.redis_client")

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    if not settings.REDIS_URL:
        return None

    try:
        raw_url = settings.REDIS_URL.get_secret_value()
        _redis_client = redis.Redis.from_url(
            raw_url,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
        )
        return _redis_client
    except Exception as exc:
        logger.error("Failed to initialize Redis client: %s", exc)
        return None