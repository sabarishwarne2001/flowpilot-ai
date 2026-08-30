"""ARCH-10 Step 2, ARCH-14 Step 1b & ARCH-14 Step 14.3 — the metering write path and bounded spend reads."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.principal import Principal, PrincipalKind, get_current_principal
from app.core.usage_events import (
    EmissionKind,
    MAX_EVENT_TYPE_LENGTH,
    UsageEventType,
    resolve,
)
from app.models.usage_event import UsageEvent
from app.models.usage_rollup import TOTAL_EVENT_TYPE, UsageRollup

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
    cost_basis_micros: Optional[int] = None,
    cost_basis_source: Optional[str] = None,
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

    # ---- ARCH-18: the cost basis --------------------------------------
    # Validated in Python for a readable error, then written and enforced by
    # four CHECK constraints. Passing neither is legal and common: it records
    # an honest unknown, which is the correct state for any unit whose
    # supplier rate nobody has entered yet.
    if cost_basis_micros is not None or cost_basis_source is not None:
        from app.services.cost_basis_service import (
            InvalidCostBasisError,
            validate_cost_basis,
        )

        try:
            checked_basis, checked_source = validate_cost_basis(
                cost_basis_micros, cost_basis_source
            )
        except InvalidCostBasisError as exc:
            raise UsageError(str(exc)) from exc

        resolved_cost_basis = (
            int(checked_basis) if checked_basis is not None else None
        )
        resolved_cost_source = checked_source
    else:
        resolved_cost_basis = None
        resolved_cost_source = None

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
        cost_basis_micros=resolved_cost_basis,
        cost_basis_source=resolved_cost_source,
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
            "cost_basis_micros": resolved_cost_basis,
            "cost_basis_source": resolved_cost_source,
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


# ============================================================================
# Read helpers — used by Step 3 and direct verification
# ============================================================================


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


# ============================================================================
# ARCH-14 Step 14.3 — bounded reads (finding B2)
# ============================================================================


@dataclass(frozen=True)
class ReadProfile:
    """How many rows each half of a bounded read actually touched."""

    rollup_rows: int
    event_rows: int

    @property
    def total(self) -> int:
        return self.rollup_rows + self.event_rows


def _is_hour_aligned(moment: datetime) -> bool:
    if moment.minute or moment.second or moment.microsecond:
        logger.error(
            "usage.bounded_read_unaligned_since",
            extra={"since": moment.isoformat()},
        )
        return False
    return True


def _rollup_totals(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: datetime,
    event_types: Optional[Iterable[str]],
) -> dict[str, tuple[Decimal, int]]:
    stmt = (
        select(
            UsageRollup.event_type,
            func.coalesce(func.sum(UsageRollup.quantity), 0),
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
        )
        .where(
            UsageRollup.organization_id == organization_id,
            UsageRollup.grain == "ORG_TOTAL",
            UsageRollup.granularity == "HOUR",
            UsageRollup.bucket_start >= since,
            UsageRollup.bucket_start <= until,
        )
        .group_by(UsageRollup.event_type)
    )
    if event_types is not None:
        stmt = stmt.where(UsageRollup.event_type.in_(list(event_types)))
    else:
        stmt = stmt.where(UsageRollup.event_type != TOTAL_EVENT_TYPE)

    return {
        row[0]: (Decimal(row[1]), int(row[2])) for row in db.execute(stmt).all()
    }


def _tail_totals(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime],
    event_types: Optional[Iterable[str]],
) -> dict[str, tuple[Decimal, int]]:
    stmt = (
        select(
            UsageEvent.event_type,
            func.coalesce(func.sum(UsageEvent.quantity), 0),
            func.coalesce(func.sum(UsageEvent.cost_micros), 0),
        )
        .where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.aggregated_at.is_(None),
            UsageEvent.occurred_at >= since,
        )
        .group_by(UsageEvent.event_type)
    )
    if until is not None:
        stmt = stmt.where(UsageEvent.occurred_at < until)
    if event_types is not None:
        wanted = [t for t in event_types if t != TOTAL_EVENT_TYPE]
        if not wanted:
            return {}
        stmt = stmt.where(UsageEvent.event_type.in_(wanted))

    return {
        row[0]: (Decimal(row[1]), int(row[2])) for row in db.execute(stmt).all()
    }


def usage_totals_bounded(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
    event_types: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> dict[str, tuple[Decimal, int]]:
    """`usage_totals`, read from rollups plus the unfolded tail."""
    if not getattr(settings, "SPEND_USE_ROLLUP_READS", True):
        return usage_totals(
            db,
            organization_id=organization_id,
            since=since,
            until=until,
            event_types=event_types,
        )
    if not _is_hour_aligned(since):
        return usage_totals(
            db,
            organization_id=organization_id,
            since=since,
            until=until,
            event_types=event_types,
        )

    horizon = until or (now or datetime.now(timezone.utc))
    merged = _rollup_totals(
        db,
        organization_id=organization_id,
        since=since,
        until=horizon,
        event_types=event_types,
    )
    for event_type, (qty, cost) in _tail_totals(
        db,
        organization_id=organization_id,
        since=since,
        until=until,
        event_types=event_types,
    ).items():
        prior_qty, prior_cost = merged.get(event_type, (Decimal(0), 0))
        merged[event_type] = (prior_qty + qty, prior_cost + cost)
    return merged


def total_cost_micros_bounded(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> int:
    """Total cost across every event type, in at most 744 + backlog rows."""
    if not getattr(settings, "SPEND_USE_ROLLUP_READS", True) or not _is_hour_aligned(
        since
    ):
        return total_cost_micros(
            db, organization_id=organization_id, since=since, until=until
        )

    horizon = until or (now or datetime.now(timezone.utc))

    rolled = db.execute(
        select(func.coalesce(func.sum(UsageRollup.cost_micros), 0)).where(
            UsageRollup.organization_id == organization_id,
            UsageRollup.grain == "ORG_TOTAL",
            UsageRollup.granularity == "HOUR",
            UsageRollup.event_type == TOTAL_EVENT_TYPE,
            UsageRollup.bucket_start >= since,
            UsageRollup.bucket_start <= horizon,
        )
    ).scalar_one()

    tail_stmt = select(func.coalesce(func.sum(UsageEvent.cost_micros), 0)).where(
        UsageEvent.organization_id == organization_id,
        UsageEvent.aggregated_at.is_(None),
        UsageEvent.occurred_at >= since,
    )
    if until is not None:
        tail_stmt = tail_stmt.where(UsageEvent.occurred_at < until)

    return int(rolled) + int(db.execute(tail_stmt).scalar_one())


def bounded_read_profile(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
    event_type: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ReadProfile:
    """Count the rows a bounded read touches. For Gate 14.3 and for ops."""
    horizon = until or (now or datetime.now(timezone.utc))

    rollup_stmt = select(func.count()).select_from(UsageRollup).where(
        UsageRollup.organization_id == organization_id,
        UsageRollup.grain == "ORG_TOTAL",
        UsageRollup.granularity == "HOUR",
        UsageRollup.bucket_start >= since,
        UsageRollup.bucket_start <= horizon,
    )
    event_stmt = select(func.count()).select_from(UsageEvent).where(
        UsageEvent.organization_id == organization_id,
        UsageEvent.aggregated_at.is_(None),
        UsageEvent.occurred_at >= since,
    )
    if event_type is not None:
        rollup_stmt = rollup_stmt.where(UsageRollup.event_type == event_type)
        if event_type != TOTAL_EVENT_TYPE:
            event_stmt = event_stmt.where(UsageEvent.event_type == event_type)
    if until is not None:
        event_stmt = event_stmt.where(UsageEvent.occurred_at < until)

    return ReadProfile(
        rollup_rows=int(db.execute(rollup_stmt).scalar_one()),
        event_rows=int(db.execute(event_stmt).scalar_one()),
    )