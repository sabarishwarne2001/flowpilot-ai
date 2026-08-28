"""ARCH-15 Steps 15.1 / 15.2 — the inbound door.

    verify -> persist -> acknowledge -> hand to a job

and nothing else in the request. Stripe times out at 20 seconds and retries on
any non-2xx; doing real work in the handler turns one slow Stripe API call into
a duplicate delivery, and then into two reconciles racing each other. The
endpoint therefore does four things: read the raw bytes, verify, insert on
conflict do nothing, return 200.

TERMINAL STATUS IS A DECISION
=============================

`IGNORED` is separate from `PROCESSED` because most Stripe event types are ones
we do not act on. Recording them as PROCESSED makes "did we handle this?"
unanswerable at exactly the moment somebody is asking it — six months later,
because a subscription is in a state nobody can explain. `result` carries the
reason.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.stripe_inbound_event import (
    StripeInboundEvent,
    StripeInboundStatus,
)
from app.services.billing.stripe_gateway import StripeEvent
from app.workers.claim import (
    STRIPE_INBOUND_QUEUE,
    claim_eligible_rows,
    release_expired_leases,
)

logger = logging.getLogger("app.services.billing.inbound")

#: Backoff for a failed reconcile. Shorter than the outbox default because an
#: inbound failure means billing state is currently wrong, not that a customer's
#: endpoint is down.
RETRY_BASE_SECONDS: int = 15
RETRY_CEILING_SECONDS: int = 30 * 60


class InboundEventError(Exception):
    """Base class for ingestion refusals."""


class LivemodeMismatchError(InboundEventError):
    """A test-mode event arrived at a live-mode deployment, or the reverse."""


def _sanitize_for_json(val: Any) -> Any:
    """Recursively convert Decimals and non-JSON native types."""
    if isinstance(val, Decimal):
        return float(val) if val % 1 else int(val)
    if isinstance(val, dict):
        return {str(k): _sanitize_for_json(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_sanitize_for_json(x) for x in val]
    return val


def _truncate(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _backoff(attempts: int) -> timedelta:
    exponent = max(0, min(int(attempts), 16))
    seconds = min(RETRY_CEILING_SECONDS, RETRY_BASE_SECONDS * (2**exponent))
    return timedelta(seconds=seconds)


def assert_livemode(event: StripeEvent) -> None:
    """Refuse an event from the wrong Stripe mode."""
    expected = bool(settings.STRIPE_LIVEMODE)
    if bool(event.livemode) is not expected:
        raise LivemodeMismatchError(
            f"Event {event.id} has livemode={event.livemode} but this "
            f"deployment runs with STRIPE_LIVEMODE={expected}. Refusing: a "
            "mode-crossed event writes fixture state over real billing state."
        )


def resolve_organization_id(db: Session, event: StripeEvent) -> Optional[uuid.UUID]:
    """Best-effort tenancy, discovered from the payload."""
    from app.models.billing_account import BillingAccount

    obj = event.data_object
    customer_id: Optional[str] = None

    raw_customer = obj.get("customer")
    if isinstance(raw_customer, dict):
        customer_id = raw_customer.get("id")
    elif raw_customer:
        customer_id = str(raw_customer)
    elif str(obj.get("object") or "") == "customer" and obj.get("id"):
        customer_id = str(obj["id"])

    # Fallback to metadata on subscription or checkout objects
    raw_org = (obj.get("metadata") or {}).get("organization_id")
    if not raw_org and obj.get("subscription_data"):
        raw_org = (obj.get("subscription_data", {}).get("metadata") or {}).get("organization_id")

    if raw_org:
        try:
            return uuid.UUID(str(raw_org))
        except (TypeError, ValueError):
            pass

    if not customer_id:
        return None

    return db.execute(
        select(BillingAccount.organization_id).where(
            BillingAccount.stripe_customer_id == customer_id
        )
    ).scalar_one_or_none()


def record_event(
    db: Session,
    *,
    event: StripeEvent,
    signature_header: str,
    organization_id: Optional[uuid.UUID] = None,
) -> tuple[Optional[uuid.UUID], bool]:
    """Persist a verified event exactly once."""
    assert_livemode(event)

    values: dict[str, Any] = {
        "stripe_event_id": event.id,
        "event_type": event.type,
        "api_version": event.api_version,
        "stripe_created_at": event.created,
        "livemode": bool(event.livemode),
        "payload": _sanitize_for_json(dict(event.payload or {})),
        "signature_header": signature_header,
        "organization_id": organization_id,
        "status": StripeInboundStatus.PENDING.value,
        "max_attempts": int(settings.STRIPE_INBOUND_MAX_ATTEMPTS),
    }

    stmt = (
        pg_insert(StripeInboundEvent.__table__)
        .values(**values)
        .on_conflict_do_nothing(
            constraint="uq_stripe_inbound_events_event_id"
        )
        .returning(StripeInboundEvent.__table__.c.id)
    )

    row_id = db.execute(stmt).scalar_one_or_none()
    created = row_id is not None

    logger.info(
        "stripe_inbound.recorded" if created else "stripe_inbound.replayed",
        extra={
            "stripe_event_id": event.id,
            "event_type": event.type,
            "is_created": created,
            "organization_id": str(organization_id) if organization_id else None,
        },
    )
    return row_id, created


# ============================================================================
# Claim path
# ============================================================================


def claim_batch(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    batch_size: Optional[int] = None,
    lease_seconds: Optional[int] = None,
) -> list[StripeInboundEvent]:
    return claim_eligible_rows(
        db,
        STRIPE_INBOUND_QUEUE,
        worker_id=worker_id,
        batch_size=batch_size or int(settings.STRIPE_INBOUND_BATCH_SIZE),
        lease_seconds=lease_seconds or int(settings.STRIPE_INBOUND_LEASE_SECONDS),
    )


def reap_expired_leases(db: Session, *, limit: int = 500) -> int:
    return release_expired_leases(db, STRIPE_INBOUND_QUEUE, limit=limit)


# ============================================================================
# Result marking
# ============================================================================


def _terminal(
    db: Session,
    event_id: uuid.UUID,
    *,
    status: StripeInboundStatus,
    result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    db.execute(
        update(StripeInboundEvent)
        .where(StripeInboundEvent.id == event_id)
        .values(
            status=status,
            processed_at=func.now(),
            claim_expires_at=None,
            last_error=_truncate(error) if error else None,
            result=result,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def mark_processed(
    db: Session, event_id: uuid.UUID, *, result: Optional[dict[str, Any]] = None
) -> None:
    _terminal(db, event_id, status=StripeInboundStatus.PROCESSED, result=result)


def mark_ignored(
    db: Session, event_id: uuid.UUID, *, reason: str, detail: Optional[str] = None
) -> None:
    _terminal(
        db,
        event_id,
        status=StripeInboundStatus.IGNORED,
        result={"ignored_reason": reason, "detail": detail},
    )


def mark_failed(
    db: Session,
    event_id: uuid.UUID,
    *,
    attempts: int,
    max_attempts: int,
    error: str,
) -> StripeInboundStatus:
    if attempts >= max_attempts:
        _terminal(
            db, event_id, status=StripeInboundStatus.DEAD, error=error,
            result={"dead_reason": "attempt_ceiling", "attempts": attempts},
        )
        logger.error(
            "stripe_inbound.dead_letter",
            extra={
                "stripe_inbound_event_id": str(event_id),
                "attempts": attempts,
                "error": _truncate(error, 512),
            },
        )
        return StripeInboundStatus.DEAD

    db.execute(
        update(StripeInboundEvent)
        .where(StripeInboundEvent.id == event_id)
        .values(
            status=StripeInboundStatus.FAILED,
            claim_expires_at=None,
            available_at=datetime.now(timezone.utc) + _backoff(attempts),
            last_error=_truncate(error),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return StripeInboundStatus.FAILED


def attach_organization(
    db: Session, event_id: uuid.UUID, *, organization_id: uuid.UUID
) -> None:
    db.execute(
        update(StripeInboundEvent)
        .where(
            StripeInboundEvent.id == event_id,
            StripeInboundEvent.organization_id.is_(None),
        )
        .values(organization_id=organization_id, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


# ============================================================================
# Operator reads
# ============================================================================


def backlog_depth(db: Session) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(StripeInboundEvent)
            .where(
                StripeInboundEvent.status.in_(
                    [StripeInboundStatus.PENDING, StripeInboundStatus.FAILED]
                )
            )
        ).scalar_one()
    )


def dead_letters(
    db: Session, *, limit: int = 100
) -> Sequence[StripeInboundEvent]:
    return list(
        db.execute(
            select(StripeInboundEvent)
            .where(StripeInboundEvent.status == StripeInboundStatus.DEAD)
            .order_by(StripeInboundEvent.received_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def get_by_stripe_event_id(
    db: Session, stripe_event_id: str
) -> Optional[StripeInboundEvent]:
    return db.execute(
        select(StripeInboundEvent).where(
            StripeInboundEvent.stripe_event_id == stripe_event_id
        )
    ).scalar_one_or_none()


__all__ = [
    "InboundEventError",
    "LivemodeMismatchError",
    "assert_livemode",
    "attach_organization",
    "backlog_depth",
    "claim_batch",
    "dead_letters",
    "get_by_stripe_event_id",
    "mark_failed",
    "mark_ignored",
    "mark_processed",
    "reap_expired_leases",
    "record_event",
    "resolve_organization_id",
]