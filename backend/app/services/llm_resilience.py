"""ARCH-11.5 Step 2 — LLM provider resilience.

Provides transient vs. permanent error classification, full-jitter exponential backoff,
per-provider circuit breaker protection, deadline enforcement, and optional failover.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from app.core.breaker import BreakerOpen, CircuitBreaker, get_breaker
from app.core.config import settings

logger = logging.getLogger("app.services.llm_resilience")


class FailureClass(str, Enum):
    TRANSIENT = "TRANSIENT"
    RATE_LIMITED = "RATE_LIMITED"
    PERMANENT = "PERMANENT"
    REFUSED = "REFUSED"


@dataclass
class ProviderAttempt:
    provider: str
    attempt: int
    elapsed_ms: float
    outcome: str
    failure_class: Optional[FailureClass] = None
    error: Optional[str] = None


@dataclass
class LLMCallOutcome:
    value: Any
    provider: str
    attempts: list[ProviderAttempt] = field(default_factory=list)
    failed_over: bool = False
    total_ms: float = 0.0

    def as_details(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "attempts": len(self.attempts),
            "failed_over": self.failed_over,
            "total_ms": round(self.total_ms, 1),
            "trail": [
                {
                    "provider": a.provider,
                    "attempt": a.attempt,
                    "outcome": a.outcome,
                    "class": a.failure_class.value if a.failure_class else None,
                    "elapsed_ms": round(a.elapsed_ms, 1),
                }
                for a in self.attempts
            ],
        }


class LLMUnavailable(RuntimeError):
    """Every attempt against every eligible provider failed."""

    def __init__(self, message: str, *, attempts: list[ProviderAttempt]) -> None:
        super().__init__(message)
        self.attempts = attempts


class LLMPermanentError(RuntimeError):
    """The request will never succeed as sent. Not retried, not failed over."""


_PERMANENT_MARKERS: tuple[str, ...] = (
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission_denied",
    "invalid_request",
    "context_length",
    "maximum context length",
    "model_not_found",
    "does not exist",
    "unsupported",
    "content_filter",
    "safety",
    "blocked",
)

_TRANSIENT_MARKERS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "unavailable",
    "overloaded",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "resource has been exhausted",
    "deadline exceeded",
)

_STATUS_PERMANENT = {400, 401, 403, 404, 405, 409, 413, 422}
_STATUS_TRANSIENT = {408, 425, 500, 502, 503, 504}


def classify(exc: BaseException) -> FailureClass:
    """Decide whether exc is worth another attempt."""
    from app.core.exceptions import SpendLimitExceededError

    if isinstance(exc, SpendLimitExceededError):
        return FailureClass.REFUSED

    name = type(exc).__name__.lower()
    if "ratelimit" in name or "toomanyrequests" in name:
        return FailureClass.RATE_LIMITED

    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    if isinstance(status, int):
        if status == 429:
            return FailureClass.RATE_LIMITED
        if status in _STATUS_PERMANENT:
            return FailureClass.PERMANENT
        if status in _STATUS_TRANSIENT:
            return FailureClass.TRANSIENT

    message = f"{name} {exc}".lower()
    if any(marker in message for marker in _PERMANENT_MARKERS):
        return FailureClass.PERMANENT
    if any(marker in message for marker in _TRANSIENT_MARKERS):
        return FailureClass.TRANSIENT

    logger.warning(
        "llm.unclassified_error",
        extra={"error_type": type(exc).__name__},
    )
    return FailureClass.TRANSIENT


def backoff_delay(attempt: int, *, base: float, cap: float, rate_limited: bool) -> float:
    window = min(cap, base * (2 ** max(0, attempt - 1)))
    if rate_limited:
        window = min(cap, window * 2)
    return random.uniform(0.0, window)


def provider_breaker(provider: str) -> CircuitBreaker:
    return get_breaker(
        f"llm:{provider}",
        failure_threshold=settings.LLM_BREAKER_THRESHOLD,
        reset_after=settings.LLM_BREAKER_RESET_SECONDS,
    )


def execute(
    call: Callable[[str], Any],
    *,
    provider: str,
    fallback_provider: Optional[str] = None,
    deadline_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMCallOutcome:
    started = time.monotonic()
    budget = float(deadline_seconds or settings.LLM_REQUEST_DEADLINE_SECONDS)
    attempts_allowed = int(max_attempts or settings.LLM_MAX_ATTEMPTS)
    trail: list[ProviderAttempt] = []

    providers = [provider]
    if (
        settings.LLM_FAILOVER_ENABLED
        and fallback_provider
        and fallback_provider != provider
    ):
        providers.append(fallback_provider)

    for index, current in enumerate(providers):
        breaker = provider_breaker(current)

        for attempt in range(1, attempts_allowed + 1):
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                trail.append(
                    ProviderAttempt(
                        provider=current,
                        attempt=attempt,
                        elapsed_ms=0.0,
                        outcome="deadline_exhausted",
                    )
                )
                break

            call_started = time.monotonic()
            try:
                value = breaker.call(call, current)
            except BreakerOpen as exc:
                trail.append(
                    ProviderAttempt(
                        provider=current,
                        attempt=attempt,
                        elapsed_ms=(time.monotonic() - call_started) * 1000,
                        outcome="short_circuited",
                        error=str(exc),
                    )
                )
                break
            except BaseException as exc:  # noqa: BLE001
                failure = classify(exc)
                trail.append(
                    ProviderAttempt(
                        provider=current,
                        attempt=attempt,
                        elapsed_ms=(time.monotonic() - call_started) * 1000,
                        outcome="failed",
                        failure_class=failure,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

                if failure is FailureClass.REFUSED:
                    raise
                if failure is FailureClass.PERMANENT:
                    logger.warning(
                        "llm.permanent_failure",
                        extra={"provider": current, "error": type(exc).__name__},
                    )
                    raise LLMPermanentError(str(exc)) from exc

                if attempt >= attempts_allowed:
                    break

                delay = backoff_delay(
                    attempt,
                    base=settings.LLM_BACKOFF_BASE_SECONDS,
                    cap=settings.LLM_BACKOFF_CAP_SECONDS,
                    rate_limited=failure is FailureClass.RATE_LIMITED,
                )
                remaining = budget - (time.monotonic() - started)
                if delay >= remaining:
                    trail.append(
                        ProviderAttempt(
                            provider=current,
                            attempt=attempt,
                            elapsed_ms=0.0,
                            outcome="deadline_would_expire",
                        )
                    )
                    break
                sleep(delay)
                continue

            total_ms = (time.monotonic() - started) * 1000
            trail.append(
                ProviderAttempt(
                    provider=current,
                    attempt=attempt,
                    elapsed_ms=(time.monotonic() - call_started) * 1000,
                    outcome="ok",
                )
            )
            outcome = LLMCallOutcome(
                value=value,
                provider=current,
                attempts=trail,
                failed_over=index > 0,
                total_ms=total_ms,
            )
            if outcome.failed_over:
                logger.warning(
                    "llm.failed_over",
                    extra={"from": providers[0], "to": current},
                )
            logger.info("llm.call_complete", extra=outcome.as_details())
            return outcome

    total_ms = (time.monotonic() - started) * 1000
    logger.error(
        "llm.exhausted",
        extra={
            "providers": providers,
            "attempts": len(trail),
            "total_ms": round(total_ms, 1),
        },
    )
    raise LLMUnavailable(
        "Every attempt against every eligible provider failed within the "
        f"{budget:.1f}s request deadline.",
        attempts=trail,
    )


__all__ = [
    "FailureClass",
    "LLMCallOutcome",
    "LLMPermanentError",
    "LLMUnavailable",
    "ProviderAttempt",
    "backoff_delay",
    "classify",
    "execute",
    "provider_breaker",
]