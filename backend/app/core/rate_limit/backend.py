"""Rate limit storage backends (ARCH-08 §B.5, §6.2, §6.3, §11.2)."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import redis
from app.core.config import settings

logger = logging.getLogger("app.core.rate_limit.backend")

_LUA_SLIDING_WINDOW_COUNTER = """
local limit    = tonumber(ARGV[1])
local window   = tonumber(ARGV[2])
local elapsed  = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local current  = tonumber(redis.call('GET', KEYS[1])) or 0
local previous = tonumber(redis.call('GET', KEYS[2])) or 0

local weight    = (window - elapsed) / window
local estimated = previous * weight + current

if estimated + cost > limit then
  return {0, math.floor(limit - estimated), window - elapsed}
end

local total = redis.call('INCRBY', KEYS[1], cost)
if total == cost then
  redis.call('EXPIRE', KEYS[1], window * 2)
end
return {1, math.floor(limit - estimated - cost), window - elapsed}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    reset_seconds: int


class RateLimitBackend(ABC):
    @abstractmethod
    def consume(
        self, *, key: str, limit: int, window_seconds: int, cost: int = 1
    ) -> RateLimitDecision:
        ...


class RedisBackend(RateLimitBackend):
    def __init__(self, client: redis.Redis) -> None:
        self.client = client
        self._sha: Optional[str] = None

    def _eval_script(
        self, keys: list[str], args: list[str | int]
    ) -> list[int]:
        if self._sha is None:
            self._sha = self.client.script_load(_LUA_SLIDING_WINDOW_COUNTER)
        try:
            return self.client.evalsha(self._sha, len(keys), *keys, *args)
        except redis.exceptions.NoScriptError:
            self._sha = self.client.script_load(_LUA_SLIDING_WINDOW_COUNTER)
            return self.client.evalsha(self._sha, len(keys), *keys, *args)

    def consume(
        self, *, key: str, limit: int, window_seconds: int, cost: int = 1
    ) -> RateLimitDecision:
        now = time.time()
        current_window_idx = int(now // window_seconds)
        prev_window_idx = current_window_idx - 1
        elapsed_in_window = int(now % window_seconds)

        key_curr = f"{key}:{current_window_idx}"
        key_prev = f"{key}:{prev_window_idx}"

        res = self._eval_script(
            [key_curr, key_prev],
            [limit, window_seconds, elapsed_in_window, cost],
        )
        allowed = bool(res[0])
        remaining = int(res[1])
        reset_secs = int(res[2])
        return RateLimitDecision(
            allowed=allowed, remaining=remaining, reset_seconds=reset_secs
        )


class InMemoryBackend(RateLimitBackend):
    def __init__(self) -> None:
        if settings.ENVIRONMENT != "test":
            raise RuntimeError(
                "InMemoryBackend is strictly forbidden outside ENVIRONMENT=test."
            )
        self._counters: dict[str, int] = {}
        self._timestamps: dict[str, float] = {}

    def consume(
        self, *, key: str, limit: int, window_seconds: int, cost: int = 1
    ) -> RateLimitDecision:
        now = time.time()
        last_time = self._timestamps.get(key, now)
        if now - last_time > window_seconds:
            self._counters[key] = 0

        current = self._counters.get(key, 0)
        if current + cost > limit:
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                reset_seconds=int(window_seconds - (now % window_seconds)),
            )

        self._counters[key] = current + cost
        self._timestamps[key] = now
        remaining = max(0, limit - (current + cost))
        reset_secs = int(window_seconds - (now % window_seconds))
        return RateLimitDecision(
            allowed=True, remaining=remaining, reset_seconds=reset_secs
        )
