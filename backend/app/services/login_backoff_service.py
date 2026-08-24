"""Login failure accounting (ARCH-08 §B.6, §7.1; extended by SEC-1 Tranche 3)."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from app.core.config import settings
from app.core.rate_limit.policy import (
    POLICY_LOGIN_ACCOUNT,
    POLICY_LOGIN_ACCOUNT_IP,
    LoginGuardPolicy,
    LoginScopeBehaviour,
)
from app.core.redis_client import get_redis_client

logger = logging.getLogger("app.services.login_backoff")

_KEY_TTL_SECONDS = 3600


@dataclass(frozen=True)
class BackoffStatus:
    is_backed_off: bool
    retry_after_seconds: int
    delay_ms: int = 0


# ===========================================================================
# Storage
# ===========================================================================


class CounterStore(Protocol):
    def incr(self, key: str, ttl: int) -> int: ...
    def get(self, key: str) -> Optional[str]: ...
    def setex(self, key: str, value: str, ttl: int) -> None: ...
    def delete(self, *keys: str) -> None: ...


class _MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires <= time.time():
            self._data.pop(key, None)
            return None
        return value

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            current = int(self._live(key) or 0) + 1
            self._data[key] = (str(current), time.time() + ttl)
            return current

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._live(key)

    def setex(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + ttl)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class _RedisStore:
    def __init__(self, client) -> None:
        self.client = client

    def incr(self, key: str, ttl: int) -> int:
        count = int(self.client.incr(key))
        self.client.expire(key, ttl)
        return count

    def get(self, key: str) -> Optional[str]:
        value = self.client.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    def setex(self, key: str, value: str, ttl: int) -> None:
        self.client.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> None:
        if keys:
            self.client.delete(*keys)


_fallback_store = _MemoryStore()
_store_override: Optional[CounterStore] = None


def set_store(store: Optional[CounterStore]) -> Optional[CounterStore]:
    global _store_override
    previous = _store_override
    _store_override = store
    return previous


def reset_store() -> None:
    set_store(None)
    _fallback_store.clear()


def _store() -> CounterStore:
    if _store_override is not None:
        return _store_override
    client = get_redis_client()
    if client is None:
        logger.error(
            "login_backoff.degraded_to_process_local_store",
            extra={"reason": "redis_unavailable"},
        )
        return _fallback_store
    return _RedisStore(client)


# ===========================================================================
# Keys
# ===========================================================================


def _hmac(message: str) -> str:
    secret = settings.JWT_SECRET_KEY.get_secret_value().encode("utf-8")
    return hmac.new(secret, message.encode("utf-8"), digestmod="sha256").hexdigest()[
        :32
    ]


def _pair_hmac(ip: str, email: str) -> str:
    return _hmac(f"{ip}|{(email or '').strip().lower()}")


def _account_hmac(email: str) -> str:
    return _hmac(f"account|{(email or '').strip().lower()}")


def _keys(policy: LoginGuardPolicy, digest: str) -> tuple[str, str]:
    return (
        f"bo:v2:{policy.name}:{digest}:n",
        f"bo:v2:{policy.name}:{digest}:until",
    )


def _ladder(policy: LoginGuardPolicy, count: int) -> int:
    over = max(0, count - policy.threshold)
    if over == 0:
        return 0
    return min(policy.ladder_ceiling, policy.ladder_base * (2 ** (over - 1)))


# ===========================================================================
# Decision
# ===========================================================================


def _scope_status(policy: LoginGuardPolicy, digest: str) -> BackoffStatus:
    count_key, until_key = _keys(policy, digest)
    store = _store()

    try:
        raw_until = store.get(until_key)
    except Exception as exc:  # noqa: BLE001
        logger.error("login_backoff.read_failed", extra={"error": str(exc)})
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    if raw_until is None:
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    try:
        until_ts = float(raw_until)
    except (TypeError, ValueError):
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    remaining = until_ts - time.time()
    if remaining <= 0:
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    if policy.behaviour is LoginScopeBehaviour.REFUSE:
        return BackoffStatus(
            is_backed_off=True, retry_after_seconds=max(int(remaining) + 1, 1)
        )

    return BackoffStatus(
        is_backed_off=False,
        retry_after_seconds=0,
        delay_ms=min(int(remaining * 1000), policy.ladder_ceiling),
    )


def check_login_backoff(ip: str, email: str) -> BackoffStatus:
    if not settings.LOGIN_BACKOFF_ENABLED:
        return BackoffStatus(is_backed_off=False, retry_after_seconds=0)

    pair = _scope_status(POLICY_LOGIN_ACCOUNT_IP, _pair_hmac(ip, email))
    if pair.is_backed_off:
        return pair

    account = _scope_status(POLICY_LOGIN_ACCOUNT, _account_hmac(email))
    return BackoffStatus(
        is_backed_off=False,
        retry_after_seconds=0,
        delay_ms=max(pair.delay_ms, account.delay_ms),
    )


def record_login_failure(ip: str, email: str) -> int:
    if not settings.LOGIN_BACKOFF_ENABLED:
        return 0

    store = _store()
    pair_delay = 0

    for policy, digest in (
        (POLICY_LOGIN_ACCOUNT_IP, _pair_hmac(ip, email)),
        (POLICY_LOGIN_ACCOUNT, _account_hmac(email)),
    ):
        count_key, until_key = _keys(policy, digest)
        try:
            count = store.incr(count_key, policy.window_seconds)
            step = _ladder(policy, count)
            if step <= 0:
                continue

            if policy.behaviour is LoginScopeBehaviour.REFUSE:
                store.setex(
                    until_key, str(time.time() + step), step + _KEY_TTL_SECONDS
                )
                pair_delay = step
            else:
                store.setex(
                    until_key,
                    str(time.time() + (step / 1000.0)),
                    policy.window_seconds,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "login_backoff.write_failed",
                extra={"policy": policy.name, "error": str(exc)},
            )

    return pair_delay


def clear_login_backoff(ip: str, email: str) -> None:
    if not settings.LOGIN_BACKOFF_ENABLED:
        return

    store = _store()
    keys: list[str] = []
    keys.extend(_keys(POLICY_LOGIN_ACCOUNT_IP, _pair_hmac(ip, email)))
    keys.extend(_keys(POLICY_LOGIN_ACCOUNT, _account_hmac(email)))

    try:
        store.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.error("login_backoff.clear_failed", extra={"error": str(exc)})


def apply_delay(delay_ms: int) -> None:
    if delay_ms > 0:
        time.sleep(min(delay_ms, POLICY_LOGIN_ACCOUNT.ladder_ceiling) / 1000.0)


__all__ = [
    "BackoffStatus",
    "CounterStore",
    "apply_delay",
    "check_login_backoff",
    "clear_login_backoff",
    "record_login_failure",
    "reset_store",
    "set_store",
]