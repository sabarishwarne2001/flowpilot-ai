"""ARCH-09 §B.1 — the outbox emit path with true savepoint idempotency."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.automation_events import (
    INTERNAL_EVENT_TYPES,
    VISIBILITY_INTERNAL,
    VISIBILITY_PUBLIC,
    sorted_internal_event_types,
    visibility_for,
)
from app.core.webhook_events import (
    FORBIDDEN_EVENT_PREFIXES,
    WEBHOOK_EVENT_TYPES,
    sorted_event_types,
)
from app.models.outbox_event import HARD_DEPTH_CEILING, OutboxEvent, OutboxEventStatus

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES: int = 64 * 1024

_FORBIDDEN_PAYLOAD_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password", "passwd", "secret", "api_key", "apikey",
    "authorization", "credential", "private_key", "encrypted_",
    "hashed_", "otp", "signature",
)

_EXACT_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "token", "auth_token", "access_token", "refresh_token",
    "bearer_token", "jwt", "session_token", "token_hash",
})

_MAX_PAYLOAD_DEPTH: int = 8


class OutboxError(Exception): pass
class UnknownEventTypeError(OutboxError): pass
class ForbiddenEventTypeError(OutboxError): pass
class PayloadRejectedError(OutboxError): pass
class TransactionBoundaryError(OutboxError): pass
class VisibilityMismatchError(OutboxError): pass
class CausalityError(OutboxError): pass


def _assert_event_type(event_type: str, *, visibility: str) -> None:
    for prefix in FORBIDDEN_EVENT_PREFIXES:
        if event_type.startswith(prefix):
            raise ForbiddenEventTypeError(f"'{event_type}' is in forbidden '{prefix}*' namespace.")

    if visibility == VISIBILITY_INTERNAL:
        if event_type not in INTERNAL_EVENT_TYPES:
            raise UnknownEventTypeError(f"'{event_type}' is not an internal event type.")
        return

    if event_type in INTERNAL_EVENT_TYPES:
        raise VisibilityMismatchError(f"'{event_type}' is internal and cannot be emitted as PUBLIC.")

    if event_type not in WEBHOOK_EVENT_TYPES:
        raise UnknownEventTypeError(f"'{event_type}' is not a publishable event type.")


def _resolve_visibility(event_type: str, requested: Optional[str]) -> str:
    if requested is None:
        return visibility_for(event_type)

    normalised = str(requested).strip().upper()
    if normalised not in (VISIBILITY_PUBLIC, VISIBILITY_INTERNAL):
        raise VisibilityMismatchError(f"visibility must be PUBLIC or INTERNAL, got {requested!r}.")

    if normalised == VISIBILITY_INTERNAL:
        if event_type in WEBHOOK_EVENT_TYPES:
            raise OutboxError(f"'{event_type}' is a PUBLIC webhook event and cannot be emitted as INTERNAL.")
        return VISIBILITY_INTERNAL
    else:
        if event_type in INTERNAL_EVENT_TYPES:
            raise VisibilityMismatchError(f"'{event_type}' is internal and cannot be emitted as PUBLIC.")
        return VISIBILITY_PUBLIC


def _normalise_payload(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PayloadRejectedError("Payload must be a JSON object.")

    try:
        encoded = json.dumps(payload, default=str, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PayloadRejectedError(f"Payload is not JSON-serialisable: {exc}") from exc

    size = len(encoded.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise PayloadRejectedError(f"Payload is {size} bytes, over ceiling {MAX_PAYLOAD_BYTES}.")

    return json.loads(encoded)


def _assert_in_transaction(db: Session, *, required: bool) -> None:
    if not required:
        return
    if not db.in_transaction():
        try: db.begin()
        except Exception: pass
    if not db.in_transaction():
        raise TransactionBoundaryError("emit() was called outside an active transaction.")


def _causality(caused_by: Optional[OutboxEvent]) -> tuple[int, Optional[uuid.UUID], Optional[uuid.UUID]]:
    if caused_by is None:
        return 0, None, None
    if caused_by.id is None:
        raise CausalityError("caused_by has no id yet.")
    depth = int(caused_by.depth or 0) + 1
    if depth > HARD_DEPTH_CEILING:
        raise CausalityError(f"depth {depth} exceeds hard ceiling {HARD_DEPTH_CEILING}")
    return depth, caused_by.id, caused_by.chain_root_id


def _existing_by_idempotency_key(
    db: Session, *, organization_id: uuid.UUID, idempotency_key: Optional[str]
) -> Optional[OutboxEvent]:
    if not idempotency_key:
        return None
    return db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.organization_id == organization_id)
        .where(OutboxEvent.idempotency_key == idempotency_key)
        .limit(1)
    ).scalar_one_or_none()


def emit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    workspace_id: Optional[uuid.UUID] = None,
    resource_id: Optional[uuid.UUID] = None,
    audit_log_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    available_at: Optional[datetime] = None,
    visibility: Optional[str] = None,
    caused_by: Optional[OutboxEvent] = None,
    require_active_transaction: bool = True,
) -> OutboxEvent:
    resolved_visibility = _resolve_visibility(event_type, visibility)
    _assert_event_type(event_type, visibility=resolved_visibility)
    _assert_in_transaction(db, required=require_active_transaction)
    normalised = _normalise_payload(payload)
    depth, causation_id, correlation_id = _causality(caused_by)

    # TRUE IDEMPOTENCY: Check before inserting
    if idempotency_key:
        existing = _existing_by_idempotency_key(
            db, organization_id=organization_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            logger.info("outbox.emit_deduplicated", extra={"idempotency_key": idempotency_key})
            return existing

    event = OutboxEvent(
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=event_type,
        resource_id=resource_id,
        payload=normalised,
        audit_log_id=audit_log_id,
        idempotency_key=idempotency_key,
        status=OutboxEventStatus.PENDING,
        visibility=resolved_visibility,
        depth=depth,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    if available_at is not None:
        event.available_at = available_at

    db.add(event)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        if event in db:
            db.expunge(event)
        winner = _existing_by_idempotency_key(
            db, organization_id=organization_id, idempotency_key=idempotency_key
        )
        if winner is not None:
            return winner
        raise

    logger.info("outbox.emit", extra={"event_id": str(event.id), "event_type": event_type})
    return event


def emit_internal(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    workspace_id: Optional[uuid.UUID] = None,
    resource_id: Optional[uuid.UUID] = None,
    caused_by: Optional[OutboxEvent] = None,
    idempotency_key: Optional[str] = None,
    available_at: Optional[datetime] = None,
    require_active_transaction: bool = True,
) -> OutboxEvent:
    return emit(
        db,
        organization_id=organization_id,
        event_type=event_type,
        payload=payload,
        workspace_id=workspace_id,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        available_at=available_at,
        visibility=VISIBILITY_INTERNAL,
        caused_by=caused_by,
        require_active_transaction=require_active_transaction,
    )


# ---------------------------------------------------------------------------
# ARCH37-S1:emit-twin. Flow-builder triggers.
# ---------------------------------------------------------------------------

AUTOMATION_JOB_TYPE: str = "automation.execute"


def _ensure_automation_handler() -> None:
    """`job_service.enqueue` refuses a job type with no registered handler.

    The API and the worker register every handler at startup. A script or a
    gate that drives a service directly may not have, and a trigger must not
    fail the state change that caused it for that reason.
    """
    from app.services import job_service

    if AUTOMATION_JOB_TYPE not in job_service.JOB_HANDLERS:
        from app.workers.handlers import register_all

        register_all()


def emit_trigger(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    resource_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    caused_by: Optional[OutboxEvent] = None,
) -> Optional[OutboxEvent]:
    """Write an INTERNAL trigger event and its `automation.execute` job.

    Both rows go into the caller's transaction. Nothing relays INTERNAL events
    to the engine (OUTBOX_INTERNAL_QUEUE has no claimer), so an event written
    without its job is an event no rule ever sees.

    Returns None when there is no workspace: rules are workspace-scoped, so an
    organization-level change has no rule to run.
    """
    if workspace_id is None:
        return None
    if event_type not in INTERNAL_EVENT_TYPES:
        raise UnknownEventTypeError(f"'{event_type}' is not an internal event type.")

    from app.services import job_service

    event = emit_internal(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=event_type,
        payload=payload,
        resource_id=resource_id,
        caused_by=caused_by,
        idempotency_key=idempotency_key,
    )
    _ensure_automation_handler()
    job_service.enqueue(
        db,
        job_type=AUTOMATION_JOB_TYPE,
        organization_id=organization_id,
        payload={"outbox_event_id": str(event.id)},
        idempotency_key=f"automation:execute:{event.id}",
    )
    return event


def emit_public_with_twin(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    workspace_id: Optional[uuid.UUID] = None,
    resource_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    caused_by: Optional[OutboxEvent] = None,
    twin_resource_id: Optional[uuid.UUID] = None,
    twin_payload: Optional[dict[str, Any]] = None,
) -> tuple[OutboxEvent, Optional[OutboxEvent]]:
    """Emit a PUBLIC event and its INTERNAL `trigger.` twin, in one transaction.

    The public row is exactly what `emit` would have written. The twin carries
    the same payload plus `twin_payload`, and its `resource_id` is the work
    item the automation handler should load (`twin_resource_id`): procurement
    and radar events name a case or a finding as their resource, and the
    handler reads `resource_id` as a work item id.

    `emit` flushes inside a savepoint and never commits, so a caller that rolls
    back loses both rows together. `verify_arch37.py --db` proves it.
    """
    from app.core.automation_events import TRIGGER_TWIN_EVENT_TYPES, twin_of

    public = emit(
        db,
        organization_id=organization_id,
        event_type=event_type,
        payload=payload,
        workspace_id=workspace_id,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        caused_by=caused_by,
    )
    twin_name = twin_of(event_type)
    if twin_name not in TRIGGER_TWIN_EVENT_TYPES:
        raise UnknownEventTypeError(f"'{event_type}' has no registered internal twin.")

    merged = dict(payload or {})
    merged.update(twin_payload or {})
    merged["public_event_id"] = str(public.id)
    twin = emit_trigger(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=twin_name,
        payload=merged,
        resource_id=twin_resource_id,
        idempotency_key=(f"trigger:{idempotency_key}" if idempotency_key else None),
        caused_by=caused_by,
    )
    return public, twin


def emit_many(db: Session, events: Sequence[dict[str, Any]], *, require_active_transaction: bool = True) -> list[OutboxEvent]:
    prepared: list[OutboxEvent] = []
    _assert_in_transaction(db, required=require_active_transaction)
    for spec in events:
        event = emit(
            db,
            organization_id=spec["organization_id"],
            event_type=spec["event_type"],
            payload=spec.get("payload"),
            workspace_id=spec.get("workspace_id"),
            resource_id=spec.get("resource_id"),
            idempotency_key=spec.get("idempotency_key"),
            visibility=spec.get("visibility"),
            require_active_transaction=False,
        )
        prepared.append(event)
    return prepared


def pending_count(db: Session, *, organization_id: Optional[uuid.UUID] = None, visibility: Optional[str] = None) -> int:
    from sqlalchemy import func
    stmt = select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.status.in_([OutboxEventStatus.PENDING, OutboxEventStatus.FAILED])
    )
    if organization_id: stmt = stmt.where(OutboxEvent.organization_id == organization_id)
    if visibility: stmt = stmt.where(OutboxEvent.visibility == visibility)
    return int(db.execute(stmt).scalar_one())


def chain(db: Session, *, correlation_id: uuid.UUID, limit: int = 500) -> list[OutboxEvent]:
    from sqlalchemy import or_
    return list(db.execute(
        select(OutboxEvent)
        .where(or_(OutboxEvent.correlation_id == correlation_id, OutboxEvent.id == correlation_id))
        .order_by(OutboxEvent.seq.asc()).limit(limit)
    ).scalars().all())


def iter_dead_letters(db: Session, *, organization_id: Optional[uuid.UUID] = None, limit: int = 100) -> Iterable[OutboxEvent]:
    stmt = select(OutboxEvent).where(OutboxEvent.status == OutboxEventStatus.DEAD).order_by(OutboxEvent.created_at.desc()).limit(limit)
    if organization_id: stmt = stmt.where(OutboxEvent.organization_id == organization_id)
    return db.execute(stmt).scalars().all()
