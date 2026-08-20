"""ARCH-12 Step 2 — rate limits on generation (A2).

A MONTHLY SPEND CEILING IS NOT A RATE LIMIT
===========================================

A tenant with a 2M-token monthly budget can spend all of it in four minutes
with a `while true` loop, and the ceiling — correctly — will not stop them
until it is gone. The two controls answer different questions: a spend ceiling
protects the *business* from provider cost and clears at the end of a billing
period; a rate limit protects the *platform* from request volume and clears in
seconds. `app/core/exceptions.py` already states this distinction in
`SpendLimitExceededError`'s docstring, and it is why refusals here are 429
with `Retry-After` while quota refusals are 402.

THREE LIMITS, THREE DIFFERENT THINGS
====================================

  * **Concurrent streams per user (2).** A held connection consumes a worker
    and a database connection for the whole generation. This is the one that
    protects the process.
  * **Messages per minute per conversation (10).** Keyed on the conversation,
    not the user, because the abusive pattern is a script hammering one
    conversation and the legitimate pattern is a person with three tabs open.
  * **Concurrent streams per organization (tier value).** One enthusiastic
    tenant must not consume the whole pool.

WHY CONCURRENCY IS NOT THE SLIDING-WINDOW BACKEND
=================================================

`RateLimitBackend.consume` counts *events in a window*. Concurrency is a
count of things currently held, which needs a release. It is implemented here
as a Redis counter with a TTL well beyond the stream deadline, incremented on
acquire and decremented in a `finally`. The TTL is the safety net: a process
that dies holding a slot leaks it for at most `SLOT_TTL_SECONDS` rather than
forever, which is the failure mode a naive INCR/DECR pair has.

FAILURE MODE: OPEN
==================

Following ARCH-08 §B.5, ordinary traffic fails open when Redis is
unavailable — a cache outage must not become a full outage. Generation is
ordinary traffic; the spend ceiling is still there, still enforced in
PostgreSQL, and still the control that bounds the money.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.core.rate_limit.limiter import get_rate_limit_backend
from app.core.redis_client import get_redis_client

logger = logging.getLogger("app.services.stream_concurrency")

#: A slot outlives the longest legitimate stream but not by much. If a stream
#: could plausibly run for 120s, a 300s TTL means a crashed worker's slot is
#: reclaimed inside five minutes rather than requiring an operator.
SLOT_TTL_SECONDS: int = 300

KEY_PREFIX = "stream:concurrency:v1"


@dataclass(frozen=True)
class GenerationLimits:
    concurrent_per_user: int
    concurrent_per_organization: int
    messages_per_minute_per_conversation: int

    @classmethod
    def resolve(cls, *, tier_concurrency: Optional[int] = None) -> "GenerationLimits":
        return cls(
            concurrent_per_user=settings.STREAM_MAX_CONCURRENT_PER_USER,
            concurrent_per_organization=int(
                tier_concurrency or settings.STREAM_MAX_CONCURRENT_PER_ORG
            ),
            messages_per_minute_per_conversation=(
                settings.STREAM_MAX_MESSAGES_PER_MINUTE_PER_CONVERSATION
            ),
        )


def _enabled() -> bool:
    return bool(settings.RATE_LIMIT_ENABLED) and settings.ENVIRONMENT != "test"


def _acquire_slot(key: str, limit: int) -> bool:
    """INCR-and-check. Returns False when the caller is over the ceiling."""
    client = get_redis_client()
    if client is None:
        logger.warning("stream.concurrency_backend_unavailable", extra={"key": key})
        return True  # fail open — see the module docstring

    try:
        current = client.incr(key)
        if current == 1:
            client.expire(key, SLOT_TTL_SECONDS)
        if current > limit:
            # Roll back our own increment immediately; the refusal must not
            # keep the counter pinned above the ceiling for the TTL.
            client.decr(key)
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.warning("stream.concurrency_check_failed", exc_info=True)
        return True


def _release_slot(key: str) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        remaining = client.decr(key)
        if remaining <= 0:
            client.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("stream.concurrency_release_failed", exc_info=True)


def check_message_rate(conversation_id: uuid.UUID, *, limit: int) -> None:
    """Sliding-window limit keyed on the conversation. Raises 429 material."""
    if not _enabled():
        return

    backend = get_rate_limit_backend()
    if backend is None:
        return  # fail open

    decision = backend.consume(
        key=f"rl:v1:assistant_generate:conv:{conversation_id}",
        limit=limit,
        window_seconds=60,
    )
    if not decision.allowed:
        logger.info(
            "stream.rate_limited",
            extra={
                "conversation_id": str(conversation_id),
                "policy": "assistant_generate",
                "retry_after": decision.reset_seconds,
            },
        )
        raise RateLimitExceededError(
            "This conversation is sending messages too quickly. "
            "Wait a moment and try again.",
            retry_after=max(1, decision.reset_seconds),
            policy="assistant_generate",
        )


@contextmanager
def generation_slot(
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limits: Optional[GenerationLimits] = None,
) -> Iterator[None]:
    """Hold a concurrency slot for the life of one stream.

    Acquires user-level then organization-level. If the second fails, the
    first is released before raising — an ordering bug here leaks a slot on
    every organization-level refusal, and that leak is invisible until the
    tenant can no longer start any stream at all.
    """
    resolved = limits or GenerationLimits.resolve()
    check_message_rate(
        conversation_id, limit=resolved.messages_per_minute_per_conversation
    )

    if not _enabled():
        yield
        return

    user_key = f"{KEY_PREFIX}:user:{user_id}"
    org_key = f"{KEY_PREFIX}:org:{organization_id}"

    if not _acquire_slot(user_key, resolved.concurrent_per_user):
        logger.info(
            "stream.concurrency_refused",
            extra={"dimension": "user", "user_id": str(user_id)},
        )
        raise RateLimitExceededError(
            f"You already have {resolved.concurrent_per_user} answers being "
            "generated. Wait for one to finish.",
            retry_after=10,
            policy="assistant_concurrent_user",
        )

    if not _acquire_slot(org_key, resolved.concurrent_per_organization):
        _release_slot(user_key)
        logger.info(
            "stream.concurrency_refused",
            extra={"dimension": "organization", "organization_id": str(organization_id)},
        )
        raise RateLimitExceededError(
            "Your organization is at its limit for simultaneous AI answers. "
            "Try again shortly.",
            retry_after=15,
            policy="assistant_concurrent_org",
        )

    try:
        yield
    finally:
        _release_slot(org_key)
        _release_slot(user_key)


def current_usage(*, user_id: uuid.UUID, organization_id: uuid.UUID) -> dict[str, int]:
    """Observability helper. Never used for enforcement decisions."""
    client = get_redis_client()
    if client is None:
        return {"user": 0, "organization": 0}
    try:
        return {
            "user": int(client.get(f"{KEY_PREFIX}:user:{user_id}") or 0),
            "organization": int(client.get(f"{KEY_PREFIX}:org:{organization_id}") or 0),
        }
    except Exception:  # noqa: BLE001
        return {"user": 0, "organization": 0}


__all__ = [
    "GenerationLimits",
    "KEY_PREFIX",
    "SLOT_TTL_SECONDS",
    "check_message_rate",
    "current_usage",
    "generation_slot",
]