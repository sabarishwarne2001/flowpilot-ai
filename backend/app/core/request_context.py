"""ARCH-11.5 Step 6 — request identity, context propagation and stage timing."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

logger = logging.getLogger("app.core.request_context")

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
_workspace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "workspace_id", default=None
)
_organization_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "organization_id", default=None
)
_trace: contextvars.ContextVar[Optional["StageTrace"]] = contextvars.ContextVar(
    "stage_trace", default=None
)

#: p95 budgets in milliseconds per pipeline stage.
STAGE_BUDGETS: dict[str, float] = {
    "retrieval": 300.0,
    "retrieval.hybrid_sql": 250.0,
    "retrieval.embed_query": 80.0,
    "retrieval.intent": 30.0,
    "rerank": 200.0,
    "context_assembly": 50.0,
    "citation": 30.0,
    "vocabulary": 100.0,
    "llm": 8000.0,
}


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def context_fields() -> dict[str, Any]:
    return {
        "request_id": _request_id.get(),
        "workspace_id": _workspace_id.get(),
        "organization_id": _organization_id.get(),
    }


@dataclass
class StageRecord:
    name: str
    elapsed_ms: float
    over_budget: bool
    budget_ms: Optional[float]
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageTrace:
    request_id: str
    records: list[StageRecord] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def elapsed(self, name: str) -> Optional[float]:
        for record in self.records:
            if record.name == name:
                return record.elapsed_ms
        return None

    def as_details(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "total_ms": round(self.total_ms, 1),
            "stages": [
                {
                    "name": record.name,
                    "ms": round(record.elapsed_ms, 1),
                    "over_budget": record.over_budget,
                    **({"error": record.error} if record.error else {}),
                }
                for record in self.records
            ],
            "breaches": [
                record.name for record in self.records if record.over_budget
            ],
        }


@contextmanager
def request_scope(
    *,
    request_id: Optional[str] = None,
    workspace_id: Optional[Any] = None,
    organization_id: Optional[Any] = None,
) -> Iterator[StageTrace]:
    identifier = request_id or new_request_id()
    trace = StageTrace(request_id=identifier)

    tokens = (
        _request_id.set(identifier),
        _workspace_id.set(str(workspace_id) if workspace_id else None),
        _organization_id.set(str(organization_id) if organization_id else None),
        _trace.set(trace),
    )
    try:
        yield trace
    finally:
        logger.info("request.trace", extra={**context_fields(), **trace.as_details()})
        _trace.reset(tokens[3])
        _organization_id.reset(tokens[2])
        _workspace_id.reset(tokens[1])
        _request_id.reset(tokens[0])


@contextmanager
def stage(name: str, **details: Any) -> Iterator[dict[str, Any]]:
    budget = STAGE_BUDGETS.get(name)
    payload: dict[str, Any] = dict(details)
    started = time.perf_counter()
    error: Optional[str] = None
    try:
        yield payload
    except BaseException as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        over = bool(budget and elapsed_ms > budget)
        record = StageRecord(
            name=name,
            elapsed_ms=elapsed_ms,
            over_budget=over,
            budget_ms=budget,
            error=error,
            details=payload,
        )
        trace = _trace.get()
        if trace is not None:
            trace.records.append(record)

        log = logger.warning if (over or error) else logger.info
        log(
            f"stage.{name}",
            extra={
                **context_fields(),
                "stage": name,
                "elapsed_ms": round(elapsed_ms, 1),
                "budget_ms": budget,
                "over_budget": over,
                **({"error": error} if error else {}),
                **payload,
            },
        )


def current_trace() -> Optional[StageTrace]:
    return _trace.get()


__all__ = [
    "STAGE_BUDGETS",
    "StageRecord",
    "StageTrace",
    "context_fields",
    "current_trace",
    "get_request_id",
    "new_request_id",
    "request_scope",
    "set_request_id",
    "stage",
]