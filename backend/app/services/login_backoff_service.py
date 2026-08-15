"""Login backoff service for FlowPilot AI (ARCH-08 §B.6, §7.1).
"""

from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.core.client_ip import client_ip
from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger("app.services.login_backoff")

_MAX_BACKOFF_SECONDS = 900
_KEY_TTL_SECONDS = 3600


@dataclass(frozen=True)
class BackoffStatus:
    is_backed_off: bool
    retry_after_seconds: int


def _pair_hmac(ip: str, email: str) -> str:
    normalized_email = email.strip().lower()
    secret = settings.JWT_SECRET_KEY.get_secret_value().encode("utf-8")
    msg = f"{ip}|{normalized_email}".encode("utf-8")
    return hmac.new(secret, msg, digestmod="sha256").hexdigest()[:32]


def check_login_backoff(ip: str, email: str) -> BackoffStatus:
    # Bypass backoff during test runs
    if not settings.RATE_LIMIT_ENABLED or settings.ENVIRONMENT == "test":
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    client = get_redis_client()
    if client is None:
        return BackoffStatus(is_backed_off=True, retry_after_seconds=60)

    pair_hash = _pair_hmac(ip, email)
    until_key = f"bo:v1:pair:{pair_hash}:until"

    try:
        until_ts_raw = client.get(until_key)
        if until_ts_raw is not None:
            until_ts = float(until_ts_raw)
            now = time.time()
            if now < until_ts:
                remaining = int(until_ts - now) + 1
                return BackoffStatus(is_backed_off=True, retry_after_seconds=max(remaining, 1))
    except Exception as exc:
        logger.error("Error checking login backoff in Redis: %s", exc)
        return BackoffStatus(is_backed_off=True, retry_after_seconds=60)

    return BackoffStatus(is_backed_off=False, retry_after_seconds=0)


def record_login_failure(ip: str, email: str) -> int:
    client = get_redis_client()
    if client is None:
        return 0

    pair_hash = _pair_hmac(ip, email)
    count_key = f"bo:v1:pair:{pair_hash}:n"
    until_key = f"bo:v1:pair:{pair_hash}:until"

    try:
        count = client.incr(count_key)
        client.expire(count_key, _KEY_TTL_SECONDS)

        delay = min(2 ** (count - 1), _MAX_BACKOFF_SECONDS)
        until_ts = time.time() + delay

        client.set(until_key, str(until_ts), ex=delay + _KEY_TTL_SECONDS)
        return delay
    except Exception as exc:
        logger.error("Error recording login failure in Redis: %s", exc)
        return 0


def clear_login_backoff(ip: str, email: str) -> None:
    client = get_redis_client()
    if client is None:
        return

    pair_hash = _pair_hmac(ip, email)
    count_key = f"bo:v1:pair:{pair_hash}:n"
    until_key = f"bo:v1:pair:{pair_hash}:until"

    try:
        client.delete(count_key, until_key)
    except Exception as exc:
        logger.error("Error clearing login backoff in Redis: %s", exc)