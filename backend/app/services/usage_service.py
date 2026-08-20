"""ARCH-10 Step 2 & ARCH-14 Step 1b — the metering write path."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import Principal, PrincipalKind, get_current_principal
from app.core.usage_events import (
    EmissionKind,
    MAX_EVENT_TYPE_LENGTH,
    UsageEventType,
    resolve,
)
from app.models.usage_event import UsageEvent

logger = logging.getLogger("app.services.usage")

MAX_DETAILS_KEYS: int = 40

_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

_EXEMPT_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "truncated_tokens",
        "max_sequence_tokens",
        "billable_tokens",
        "total_billable_tokens",
        "total_truncated_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    }
)


class UsageError(Exception):
    """Base class for metering-path refusals."""


class UnknownUsageTypeError(UsageError):
    """The event type is not in the ARCH-10 vocabulary."""


class UsageQuantityError(UsageError):
    """Quantity is absent, non-positive, or unrepresentable."""


class UsageEmissionError(UsageError):
    """A SAMPLED type was emitted inline, or vice versa."""


class UsageTransactionBoundaryError(UsageError):
    """record_usage() was called outside an active transaction."""


def _assert_in_transaction(db: Session, *, required: bool) -> None:
    if not required:
        return
    if not db.in_transaction():
        try:
            db.begin()
        except Exception:  # noqa: BLE001
            pass
    if not db.in_transaction():
        raise UsageTransactionBoundaryError(
            "record_usage() was called outside an active transaction. Usage "
            "must be written in the same transaction as the work it measures."
        )


def _coerce_quantity(quantity: Any) -> Decimal:
    try:
        value = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UsageQuantityError(f"quantity {quantity!r} is not a number.") from exc
    if not value.is_finite():
        raise UsageQuantityError("quantity must be finite.")
    if value <= 0:
        raise UsageQuantityError(
            f"quantity must be > 0, got {value}. A zero-quantity usage event "
            "is noise; do not record one."
        )
    return value.quantize(Decimal("0.000001"))


def _sanitize_details(
    details: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    if not details:
        return None
    cleaned: dict[str, Any] = {}
    for index, (key, value) in enumerate(details.items()):
        if index >= MAX_DETAILS_KEYS:
            cleaned["__truncated__"] = f"{len(details) - MAX_DETAILS_KEYS} more keys"
            break
        key_str = str(key)
        key_lower = key_str.lower()
        if key_lower in _EXEMPT_DETAIL_KEYS:
            if isinstance(value, uuid.UUID):
                cleaned[key_str] = str(value)
            elif isinstance(value, Decimal):
                cleaned[key_str] = float(value)
            elif value is None or isinstance(value, (str, int, float, bool, list, dict)):
                cleaned[key_str] = value
            elif hasattr(value, "isoformat"):
                cleaned[key_str] = value.isoformat()
            else:
                cleaned[key_str] = str(value)
        elif any(f in key_lower for f in _SENSITIVE_KEY_FRAGMENTS):
            cleaned[key_str] = "[REDACTED]"
        elif isinstance(value, uuid.UUID):
            cleaned[key_str] = str(value)
        elif isinstance(value, Decimal):
            cleaned[key_str] = float(value)
        elif value is None or isinstance(value, (str, int, float, bool, list, dict)):
            cleaned[key_str] = value
        elif hasattr(value, "isoformat"):
            cleaned[key_str] = value.isoformat()
        else:
            cleaned[key_str] = str(value)
    return cleaned or None


def _attribution(
    principal: Optional[Principal],
) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID], dict[str, Any]]:
    resolved = principal if principal is not None else get_current_principal()
    if resolved is None:
        return None, None, {"principal": "UNATTRIBUTED"}
    cols = resolved.audit_columns()
    return cols["actor_id"], cols["api_key_id"], resolved.audit_details()


def record_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    quantity: Any,
    workspace_id: Optional[uuid.UUID] = None,
    cost_micros: Optional[int] = None,
    price_book_id: Optional[uuid.UUID] = None,
    unit_price_micros: Optional[Any] = None,
    provider: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[uuid.UUID] = None,
    job_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    details: Optional[Mapping[str, Any]] = None,
    principal: Optional[Principal] = None,
    allow_sampled: bool = False,
    require_active_transaction: bool = True,
) -> UsageEvent:
    """Record one billable occurrence. Flushes; the caller commits."""
    if organization_id is None:
        raise UsageError("usage_service: organization_id is required.")

    if len(event_type) > MAX_EVENT_TYPE_LENGTH:
        raise UnknownUsageTypeError(
            f"event_type exceeds {MAX_EVENT_TYPE_LENGTH} characters."
        )
    try:
        descriptor: UsageEventType = resolve(event_type)
    except ValueError as exc:
        raise UnknownUsageTypeError(str(exc)) from exc

    if descriptor.emission is EmissionKind.SAMPLED and not allow_sampled:
        raise UsageEmissionError(
            f"'{event_type}' is a SAMPLED type produced by the periodic "
            "sampler, not by inline work. If you are the sampler, pass "
            "allow_sampled=True."
        )
    if descriptor.emission is EmissionKind.OCCURRENCE and allow_sampled:
        raise UsageEmissionError(
            f"'{event_type}' is an OCCURRENCE type; allow_sampled has no "
            "meaning here and is almost certainly a copy-paste."
        )

    _assert_in_transaction(db, required=require_active_transaction)
    qty = _coerce_quantity(quantity)

    if cost_micros is not None:
        if not isinstance(cost_micros, int) or isinstance(cost_micros, bool):
            raise UsageError("cost_micros must be an int (millionths of USD).")
        if cost_micros < 0:
            raise UsageError("cost_micros must be >= 0.")

    # ARCH-14 Step 1. The two price columns travel together
    if (price_book_id is None) != (unit_price_micros is None):
        raise UsageError(
            "price_book_id and unit_price_micros must both be set or both be "
            "None. Resolve the price through pricing_service.resolve(), which "
            "returns them together."
        )

    resolved_unit_price: Optional[Decimal] = None
    if unit_price_micros is not None:
        resolved_unit_price = Decimal(str(unit_price_micros))
        if resolved_unit_price < 0:
            raise UsageError("unit_price_micros must be >= 0.")
        if cost_micros is not None:
            expected = int(
                (qty * resolved_unit_price).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            if cost_micros != expected:
                raise UsageError(
                    f"cost_micros {cost_micros} does not follow from "
                    f"quantity {qty} * unit_price_micros {resolved_unit_price} "
                    f"(= {expected}). 14.8 reproduces invoices from exactly "
                    "this arithmetic; a row that fails it is a row that cannot "
                    "be defended."
                )

    actor_id, api_key_id, principal_details = _attribution(principal)
    merged_details = {**principal_details, **(_sanitize_details(details) or {})}

    event = UsageEvent(
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=descriptor.name,
        unit=descriptor.unit.value,
        quantity=qty,
        cost_micros=cost_micros,
        price_book_id=price_book_id,
        unit_price_micros=resolved_unit_price,
        provider=provider or descriptor.default_provider,
        resource_type=resource_type,
        resource_id=resource_id,
        job_id=job_id,
        actor_id=actor_id,
        api_key_id=api_key_id,
        details=merged_details,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )

    db.add(event)
    db.flush([event])

    logger.info(
        "usage.record",
        extra={
            "usage_event_id": str(event.id),
            "usage_seq": event.seq,
            "event_type": descriptor.name,
            "quantity": str(qty),
            "cost_micros": cost_micros,
            "price_book_id": str(price_book_id) if price_book_id else None,
            "organization_id": str(organization_id),
            "job_id": str(job_id) if job_id else None,
            "principal": merged_details.get("principal"),
        },
    )
    return event


def record_usage_many(
    db: Session,
    specs: Sequence[Mapping[str, Any]],
    *,
    require_active_transaction: bool = True,
) -> list[UsageEvent]:
    _assert_in_transaction(db, required=require_active_transaction)
    return [
        record_usage(db, require_active_transaction=False, **dict(spec))
        for spec in specs
    ]


def usage_totals(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
    event_types: Optional[Iterable[str]] = None,
    for_update: bool = False,
) -> dict[str, tuple[Decimal, int]]:
    stmt = (
        select(
            UsageEvent.event_type,
            func.coalesce(func.sum(UsageEvent.quantity), 0),
            func.coalesce(func.sum(UsageEvent.cost_micros), 0),
        )
        .where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.occurred_at >= since,
        )
        .group_by(UsageEvent.event_type)
    )
    if until is not None:
        stmt = stmt.where(UsageEvent.occurred_at < until)
    if event_types is not None:
        wanted = list(event_types)
        if not wanted:
            return {}
        stmt = stmt.where(UsageEvent.event_type.in_(wanted))

    return {
        row[0]: (Decimal(row[1]), int(row[2]))
        for row in db.execute(stmt).all()
    }


def total_cost_micros(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
) -> int:
    stmt = select(func.coalesce(func.sum(UsageEvent.cost_micros), 0)).where(
        UsageEvent.organization_id == organization_id,
        UsageEvent.occurred_at >= since,
    )
    if until is not None:
        stmt = stmt.where(UsageEvent.occurred_at < until)
    return int(db.execute(stmt).scalar_one())


def is_system_attributed(event: UsageEvent) -> bool:
    return (
        event.actor_id is None
        and event.api_key_id is None
        and (event.details or {}).get("principal") == PrincipalKind.SYSTEM.value
    )