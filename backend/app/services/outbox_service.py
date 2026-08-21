"""ARCH-09 §B.1 — the outbox emit path.

ARCH-13 Step 13.1 (F1) adds the `visibility` discriminator and a second
vocabulary. Step 13.2 (A7) adds causal chain threading via `caused_by`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

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
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "encrypted_",
    "hashed_",
    "otp",
    "signature",
)

_MAX_PAYLOAD_DEPTH: int = 8


class OutboxError(Exception):
    """Base class for emit-path refusals."""


class UnknownEventTypeError(OutboxError):
    """The event type is not in the §B.2 vocabulary."""


class ForbiddenEventTypeError(OutboxError):
    """The event type is in a permanently excluded namespace."""


class PayloadRejectedError(OutboxError):
    """The payload is unserialisable, oversized, or carries a secret-shaped key."""


class TransactionBoundaryError(OutboxError):
    """emit() was called outside an active transaction."""


class VisibilityMismatchError(OutboxError):
    """ARCH-13 F1. The requested visibility contradicts the event type."""


class CausalityError(OutboxError):
    """ARCH-13 A7. The causal chain would be broken or unbounded."""


def _assert_event_type(event_type: str, *, visibility: str) -> None:
    for prefix in FORBIDDEN_EVENT_PREFIXES:
        if event_type.startswith(prefix):
            raise ForbiddenEventTypeError(
                f"'{event_type}' is in the permanently excluded '{prefix}*' "
                "namespace (ARCH-09 §B.2)."
            )

    if visibility == VISIBILITY_INTERNAL:
        if event_type not in INTERNAL_EVENT_TYPES:
            raise UnknownEventTypeError(
                f"'{event_type}' is not an internal event type. Known "
                f"internal types: {', '.join(sorted_internal_event_types())}."
            )
        return

    if event_type in INTERNAL_EVENT_TYPES:
        raise VisibilityMismatchError(
            f"'{event_type}' is an internal event type and cannot be emitted "
            "as PUBLIC."
        )

    if event_type not in WEBHOOK_EVENT_TYPES:
        raise UnknownEventTypeError(
            f"'{event_type}' is not a publishable event type. Known types: "
            f"{', '.join(sorted_event_types())}."
        )


def _resolve_visibility(event_type: str, requested: Optional[str]) -> str:
    if requested is None:
        return visibility_for(event_type)

    normalised = str(requested).strip().upper()
    if normalised not in (VISIBILITY_PUBLIC, VISIBILITY_INTERNAL):
        raise VisibilityMismatchError(
            f"visibility must be {VISIBILITY_PUBLIC!r} or "
            f"{VISIBILITY_INTERNAL!r}, got {requested!r}."
        )

    if normalised == VISIBILITY_INTERNAL:
        if event_type in WEBHOOK_EVENT_TYPES:
            raise OutboxError(
                f"'{event_type}' is a PUBLIC webhook event type and cannot be emitted as INTERNAL."
            )
        if event_type not in INTERNAL_EVENT_TYPES:
            raise UnknownEventTypeError(
                f"'{event_type}' is not an internal event type. Known internal types: {', '.join(sorted_internal_event_types())}."
            )
        return VISIBILITY_INTERNAL
    else:  # VISIBILITY_PUBLIC
        if event_type in INTERNAL_EVENT_TYPES:
            raise VisibilityMismatchError(
                f"'{event_type}' is an internal event type and cannot be emitted as PUBLIC."
            )
        if event_type not in WEBHOOK_EVENT_TYPES:
            raise UnknownEventTypeError(
                f"'{event_type}' is not a publishable event type. Known types: {', '.join(sorted_event_types())}."
            )
        return VISIBILITY_PUBLIC


def _scan_payload_keys(node: Any, *, depth: int, path: str) -> None:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise PayloadRejectedError(
            f"Payload nesting exceeds {_MAX_PAYLOAD_DEPTH} levels at '{path}'."
        )
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise PayloadRejectedError(
                    f"Non-string payload key at '{path}': {key!r}."
                )
            lowered = key.lower()
            for needle in _FORBIDDEN_PAYLOAD_KEY_SUBSTRINGS:
                if needle in lowered:
                    raise PayloadRejectedError(
                        f"Payload key '{path}{key}' matches the forbidden "
                        f"pattern '{needle}'."
                    )
            _scan_payload_keys(value, depth=depth + 1, path=f"{path}{key}.")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _scan_payload_keys(value, depth=depth + 1, path=f"{path}{index}.")


def _normalise_payload(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PayloadRejectedError(
            f"Payload must be a JSON object, got {type(payload).__name__}."
        )

    _scan_payload_keys(payload, depth=0, path="")

    try:
        encoded = json.dumps(payload, default=str, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PayloadRejectedError(
            f"Payload is not JSON-serialisable: {exc}"
        ) from exc

    size = len(encoded.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise PayloadRejectedError(
            f"Payload is {size} bytes, over the {MAX_PAYLOAD_BYTES}-byte ceiling."
        )

    return json.loads(encoded)


def _assert_in_transaction(db: Session, *, required: bool) -> None:
    if not required:
        return
    if not db.in_transaction():
        try:
            db.begin()
        except Exception:
            pass
    if not db.in_transaction():
        raise TransactionBoundaryError(
            "emit() was called outside an active transaction."
        )


def _causality(
    caused_by: Optional[OutboxEvent],
) -> tuple[int, Optional[uuid.UUID], Optional[uuid.UUID]]:
    if caused_by is None:
        return 0, None, None

    if caused_by.id is None:
        raise CausalityError(
            "caused_by has no id yet. Flush the causing event before emitting "
            "the caused one."
        )

    depth = int(caused_by.depth or 0) + 1
    if depth > HARD_DEPTH_CEILING:
        raise CausalityError(
            f"depth {depth} exceeds the hard ceiling {HARD_DEPTH_CEILING} "
            f"(correlation_id={caused_by.chain_root_id})."
        )

    return depth, caused_by.id, caused_by.chain_root_id


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
    db.flush()

    logger.info(
        "outbox.emit",
        extra={
            "outbox_event_id": str(event.id),
            "outbox_seq": event.seq,
            "event_type": event_type,
            "visibility": resolved_visibility,
            "depth": depth,
            "causation_id": str(causation_id) if causation_id else None,
            "correlation_id": str(event.chain_root_id),
            "organization_id": str(organization_id),
            "workspace_id": str(workspace_id) if workspace_id else None,
            "audit_log_id": str(audit_log_id) if audit_log_id else None,
        },
    )
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


def emit_many(
    db: Session,
    events: Sequence[dict[str, Any]],
    *,
    require_active_transaction: bool = True,
) -> list[OutboxEvent]:
    prepared: list[OutboxEvent] = []
    _assert_in_transaction(db, required=require_active_transaction)

    for index, spec in enumerate(events):
        try:
            event_type = spec["event_type"]
            organization_id = spec["organization_id"]
        except KeyError as exc:
            raise OutboxError(
                f"events[{index}] is missing required key {exc}."
            ) from exc

        resolved_visibility = _resolve_visibility(event_type, spec.get("visibility"))
        _assert_event_type(event_type, visibility=resolved_visibility)
        normalised = _normalise_payload(spec.get("payload"))
        depth, causation_id, correlation_id = _causality(spec.get("caused_by"))

        event = OutboxEvent(
            organization_id=organization_id,
            workspace_id=spec.get("workspace_id"),
            event_type=event_type,
            resource_id=spec.get("resource_id"),
            payload=normalised,
            audit_log_id=spec.get("audit_log_id"),
            idempotency_key=spec.get("idempotency_key"),
            status=OutboxEventStatus.PENDING,
            visibility=resolved_visibility,
            depth=depth,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        if spec.get("available_at") is not None:
            event.available_at = spec["available_at"]
        prepared.append(event)

    db.add_all(prepared)
    db.flush()

    for event in prepared:
        logger.info(
            "outbox.emit",
            extra={
                "outbox_event_id": str(event.id),
                "outbox_seq": event.seq,
                "event_type": event.event_type,
                "visibility": event.visibility,
                "depth": event.depth,
                "organization_id": str(event.organization_id),
            },
        )
    return prepared


def pending_count(
    db: Session,
    *,
    organization_id: Optional[uuid.UUID] = None,
    visibility: Optional[str] = None,
) -> int:
    from sqlalchemy import func, select

    stmt = select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.status.in_(
            [OutboxEventStatus.PENDING, OutboxEventStatus.FAILED]
        )
    )
    if organization_id is not None:
        stmt = stmt.where(OutboxEvent.organization_id == organization_id)
    if visibility is not None:
        stmt = stmt.where(OutboxEvent.visibility == visibility)
    return int(db.execute(stmt).scalar_one())


def chain(
    db: Session, *, correlation_id: uuid.UUID, limit: int = 500
) -> list[OutboxEvent]:
    from sqlalchemy import or_, select

    return list(
        db.execute(
            select(OutboxEvent)
            .where(
                or_(
                    OutboxEvent.correlation_id == correlation_id,
                    OutboxEvent.id == correlation_id,
                )
            )
            .order_by(OutboxEvent.seq.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def iter_dead_letters(
    db: Session, *, organization_id: Optional[uuid.UUID] = None, limit: int = 100
) -> Iterable[OutboxEvent]:
    from sqlalchemy import select

    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == OutboxEventStatus.DEAD)
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.seq.desc())
        .limit(limit)
    )
    if organization_id is not None:
        stmt = stmt.where(OutboxEvent.organization_id == organization_id)
    return db.execute(stmt).scalars().all()