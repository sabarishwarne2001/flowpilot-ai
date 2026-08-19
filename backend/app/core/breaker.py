"""ARCH-11 Step 7 — an in-process circuit breaker for internal dependencies."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger("app.core.breaker")

T = TypeVar("T")


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class BreakerOpen(RuntimeError):
    """The circuit is open; the call was not attempted."""

    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(
            f"circuit '{name}' is open; retrying in {retry_after:.1f}s"
        )
        self.name = name
        self.retry_after = retry_after


@dataclass
class BreakerSnapshot:
    name: str
    state: BreakerState
    consecutive_failures: int
    total_calls: int
    total_failures: int
    total_short_circuits: int
    opened_at: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_short_circuits": self.total_short_circuits,
            "open_for_seconds": (
                round(time.monotonic() - self.opened_at, 1)
                if self.opened_at is not None
                else None
            ),
        }


class CircuitBreaker:
    """Thread-safe, process-local. One instance per dependency, not per call."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_after: float = 30.0,
        probe_calls: int = 1,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_after = reset_after
        self._probe_calls = max(1, probe_calls)

        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None
        self._probes_in_flight = 0
        self._total_calls = 0
        self._total_failures = 0
        self._total_short_circuits = 0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._resolve_state()

    def _resolve_state(self) -> BreakerState:
        """Caller must hold the lock. Promotes OPEN to HALF_OPEN when due."""
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._reset_after
        ):
            self._state = BreakerState.HALF_OPEN
            self._probes_in_flight = 0
            logger.info("breaker.half_open", extra={"breaker": self.name})
        return self._state

    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            return BreakerSnapshot(
                name=self.name,
                state=self._resolve_state(),
                consecutive_failures=self._consecutive_failures,
                total_calls=self._total_calls,
                total_failures=self._total_failures,
                total_short_circuits=self._total_short_circuits,
                opened_at=self._opened_at,
            )

    def reset(self) -> None:
        """Force closed."""
        with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._probes_in_flight = 0

    def _admit(self) -> None:
        with self._lock:
            state = self._resolve_state()
            if state is BreakerState.CLOSED:
                self._total_calls += 1
                return
            if state is BreakerState.HALF_OPEN:
                if self._probes_in_flight >= self._probe_calls:
                    self._total_short_circuits += 1
                    raise BreakerOpen(self.name, self._reset_after)
                self._probes_in_flight += 1
                self._total_calls += 1
                return

            self._total_short_circuits += 1
            elapsed = time.monotonic() - (self._opened_at or time.monotonic())
            raise BreakerOpen(self.name, max(0.0, self._reset_after - elapsed))

    def record_success(self) -> None:
        with self._lock:
            if self._state is not BreakerState.CLOSED:
                logger.info("breaker.closed", extra={"breaker": self.name})
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._probes_in_flight = 0

    def record_failure(self, exc: Optional[BaseException] = None) -> None:
        with self._lock:
            self._total_failures += 1
            self._consecutive_failures += 1
            self._probes_in_flight = 0
            if (
                self._state is BreakerState.HALF_OPEN
                or self._consecutive_failures >= self._failure_threshold
            ):
                if self._state is not BreakerState.OPEN:
                    logger.warning(
                        "breaker.opened",
                        extra={
                            "breaker": self.name,
                            "consecutive_failures": self._consecutive_failures,
                            "error": str(exc) if exc else None,
                        },
                    )
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run func under the breaker. Raises BreakerOpen when short-circuited."""
        self._admit()
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            self.record_failure(exc)
            raise
        self.record_success()
        return result


_REGISTRY: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_breaker(
    name: str,
    *,
    failure_threshold: int = 5,
    reset_after: float = 30.0,
    probe_calls: int = 1,
) -> CircuitBreaker:
    with _REGISTRY_LOCK:
        breaker = _REGISTRY.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=failure_threshold,
                reset_after=reset_after,
                probe_calls=probe_calls,
            )
            _REGISTRY[name] = breaker
        return breaker


def all_snapshots() -> list[dict[str, Any]]:
    with _REGISTRY_LOCK:
        breakers = list(_REGISTRY.values())
    return [breaker.snapshot().as_dict() for breaker in breakers]


__all__ = [
    "BreakerOpen",
    "BreakerSnapshot",
    "BreakerState",
    "CircuitBreaker",
    "all_snapshots",
    "get_breaker",
]