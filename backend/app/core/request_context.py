"""ARCH-11.5 Step 6: request identity, context propagation and stage timing.

ARCH-17: trace/correlation propagation across the queue boundary, and the
sink that turns stage durations into something a p95 can be computed from.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Optional

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
_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)
_parent_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "parent_span_id", default=None
)
_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "span_id", default=None
)
_trace: contextvars.ContextVar[Optional[StageTrace]] = contextvars.ContextVar(
    "stage_trace", default=None
)

STAGE_BUDGETS: dict[str, float] = {
    "retrieval": 300.0,
    "retrieval.hybrid_sql": 250.0,
    "retrieval.embed_query": 80.0,
    "retrieval.intent": 30.0,
    "rerank": 200.0,
    "context_assembly": 50.0,
    "context_budget": 60.0,
    "citation": 30.0,
    "vocabulary": 100.0,
    "llm": 8000.0,
}

_TRACEPARENT_VERSION = "00"
_TRACEPARENT_FLAGS = "01"


def new_request_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def get_trace_id() -> Optional[str]:
    return _request_id.get()


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def set_correlation_id(value: Optional[Any]) -> None:
    _correlation_id.set(str(value) if value else None)


def get_organization_id() -> Optional[str]:
    return _organization_id.get()


def get_workspace_id() -> Optional[str]:
    return _workspace_id.get()


def traceparent() -> Optional[str]:
    trace_id = get_trace_id()
    if not trace_id:
        return None
    span = _span_id.get() or _parent_span_id.get()
    if not span:
        span = new_span_id()
        _span_id.set(span)
    return f"{_TRACEPARENT_VERSION}-{trace_id}-{span}-{_TRACEPARENT_FLAGS}"


def parse_traceparent(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None, None
    _, trace_id, span_id, _ = parts[0], parts[1], parts[2], parts[3]
    if len(trace_id) != 32 or len(span_id) != 16:
        return None, None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None, None
    try:
        int(trace_id, 16)
        int(span_id, 16)
    except ValueError:
        return None, None
    return trace_id, span_id


def context_fields() -> dict[str, Any]:
    return {
        "request_id": _request_id.get(),
        "workspace_id": _workspace_id.get(),
        "organization_id": _organization_id.get(),
        "trace_id": _request_id.get(),
        "correlation_id": _correlation_id.get(),
    }


TRACE_CARRIER_KEYS: tuple[str, ...] = (
    "trace_id",
    "correlation_id",
    "organization_id",
    "workspace_id",
    "parent_span_id",
)


def carrier() -> dict[str, Any]:
    """Export the current request context for propagation across job boundaries."""
    ctx_trace_id = get_trace_id()
    if ctx_trace_id is None and _correlation_id.get() is None:
        return {}

    payload = {
        "trace_id": ctx_trace_id,
        "correlation_id": _correlation_id.get(),
        "organization_id": str(_organization_id.get()) if _organization_id.get() else None,
        "workspace_id": str(_workspace_id.get()) if _workspace_id.get() else None,
        "parent_span_id": _parent_span_id.get(),
        "traceparent": traceparent(),
    }
    return payload


@dataclass
class StageRecord:
    name: str
    elapsed_ms: float
    over_budget: bool
    budget_ms: Optional[float]
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    organization_id: Optional[str] = None
    trace_id: Optional[str] = None


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
                    "error": record.error if record.error else "",
                }
                for record in self.records
            ],
            "breaches": [
                record.name for record in self.records if record.over_budget
            ],
        }


StageSink = Callable[[StageRecord], None]
_stage_sink: Optional[StageSink] = None


def set_stage_sink(sink: Optional[StageSink]) -> None:
    global _stage_sink
    _stage_sink = sink


def _emit_to_sink(record: StageRecord) -> None:
    sink = _stage_sink
    if sink is None:
        return
    try:
        sink(record)
    except Exception:  # noqa: BLE001
        logger.exception("stage.sink_failed", extra={"stage": record.name})


@contextmanager
def request_scope(
    *,
    request_id: Optional[str] = None,
    workspace_id: Optional[Any] = None,
    organization_id: Optional[Any] = None,
    correlation_id: Optional[Any] = None,
    parent_span_id: Optional[str] = None,
) -> Iterator[StageTrace]:
    identifier = request_id or new_request_id()
    span = new_span_id()
    trace = StageTrace(request_id=identifier)

    tokens = (
        _request_id.set(identifier),
        _workspace_id.set(str(workspace_id) if workspace_id else None),
        _organization_id.set(str(organization_id) if organization_id else None),
        _trace.set(trace),
        _correlation_id.set(str(correlation_id) if correlation_id else identifier),
        _parent_span_id.set(parent_span_id),
        _span_id.set(span),
    )
    try:
        yield trace
    finally:
        logger.info("request.trace", extra={**context_fields(), **trace.as_details()})
        _span_id.reset(tokens[6])
        _parent_span_id.reset(tokens[5])
        _correlation_id.reset(tokens[4])
        _trace.reset(tokens[3])
        _organization_id.reset(tokens[2])
        _workspace_id.reset(tokens[1])
        _request_id.reset(tokens[0])


@contextmanager
def job_scope(
    *,
    job_id: Any,
    job_type: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Iterator[StageTrace]:
    payload = dict(context or {})
    inherited = payload.get("trace_id")
    identifier = str(inherited) if inherited else new_request_id()
    with request_scope(
        request_id=identifier,
        organization_id=payload.get("organization_id"),
        workspace_id=payload.get("workspace_id"),
        correlation_id=payload.get("correlation_id") or identifier,
        parent_span_id=payload.get("parent_span_id"),
    ) as trace:
        logger.info(
            "job.trace_scope",
            extra={
                **context_fields(),
                "job_id": str(job_id),
                "job_type": job_type,
                "trace_origin": "INHERITED" if inherited else "ORPHAN",
            },
        )
        yield trace


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
            organization_id=_organization_id.get(),
            trace_id=_request_id.get(),
        )
        trace = _trace.get()
        if trace is not None:
            trace.records.append(record)
        _emit_to_sink(record)
        log = logger.warning if (over or error) else logger.info
        log(
            f"stage.{name}",
            extra={
                **context_fields(),
                "stage": name,
                "elapsed_ms": round(elapsed_ms, 1),
                "budget_ms": budget,
                "over_budget": over,
                "error": error if error else "",
                **payload,
            },
        )


def current_trace() -> Optional[StageTrace]:
    return _trace.get()


__all__ = [
    "STAGE_BUDGETS",
    "TRACE_CARRIER_KEYS",
    "StageRecord",
    "StageSink",
    "StageTrace",
    "carrier",
    "context_fields",
    "current_trace",
    "get_correlation_id",
    "get_organization_id",
    "get_request_id",
    "get_trace_id",
    "get_workspace_id",
    "job_scope",
    "new_request_id",
    "new_span_id",
    "parse_traceparent",
    "request_scope",
    "set_correlation_id",
    "set_request_id",
    "set_stage_sink",
    "stage",
    "traceparent",
]