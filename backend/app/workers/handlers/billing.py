"""ARCH-15 Step 15.2 / 15.4 — the `billing.*` job handlers. LIGHT profile.

LIGHT because everything here is SQL plus one HTTPS call. Nothing in the
billing path touches PaddleOCR, torch or sentence-transformers, and putting it
on a heavy image would mean paying a two-gigabyte cold start to answer a
webhook.

Three job types:

    billing.reconcile   drain the inbound queue (or one named row)
    billing.seat_sync   assert Stripe's seat count against the view
    billing.seat_drift  detect and report, without correcting

The last two are deliberately separate. Detection is a gate and must be able
to run without side effects; correction changes what a customer is charged.
Fusing them would mean the only way to find out whether drift exists is to fix
it, and then nobody ever learns how often it happens.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.principal import system_principal
from app.db.session import SessionLocal
from app.models.stripe_inbound_event import StripeInboundEvent, StripeInboundStatus
from app.services.billing import (
    inbound_service,
    reconcile_service,
    seat_service,
    stripe_gateway,
)
from app.services.billing.reconcile_service import ReconcileRefused

logger = logging.getLogger("app.workers.handlers.billing")

RECONCILE_JOB_TYPE = "billing.reconcile"
SEAT_SYNC_JOB_TYPE = "billing.seat_sync"
SEAT_DRIFT_JOB_TYPE = "billing.seat_drift"


def _int(payload: dict[str, Any], key: str, default: int) -> int:
    raw = payload.get(key)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _uuid(payload: dict[str, Any], key: str) -> Optional[uuid.UUID]:
    raw = payload.get(key)
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{key} is not a UUID: {raw!r}")


# ============================================================================
# billing.reconcile
# ============================================================================


def handle_billing_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    """Claim inbound events and reconcile each one.

    Two shapes:

    * `{}` — claim a batch and drain it. The scheduled shape.
    * `{"inbound_event_id": "..."}` — reconcile one specific row, ignoring
      the lease. The shape an operator uses to retry a dead letter after
      fixing whatever caused it.
    """
    specific = _uuid(payload, "inbound_event_id")
    if specific is not None:
        return _reconcile_one_by_id(specific)

    batch_size = _int(
        payload, "batch_size", int(settings.STRIPE_INBOUND_BATCH_SIZE)
    )
    lease_seconds = _int(
        payload, "lease_seconds", int(settings.STRIPE_INBOUND_LEASE_SECONDS)
    )

    with SessionLocal() as db:
        with system_principal(job_name="jobs.billing.reconcile"):
            reaped = inbound_service.reap_expired_leases(db)
            db.commit()

            claimed = inbound_service.claim_batch(
                db, batch_size=batch_size, lease_seconds=lease_seconds
            )
            # The claim is committed before any Stripe call. Holding a
            # transaction open across an HTTPS round trip is how a slow
            # provider turns into exhausted connections, and the lease is
            # exactly the mechanism that makes releasing the transaction safe.
            snapshot = [
                (row.id, row.attempts, row.max_attempts) for row in claimed
            ]
            db.commit()

    processed = ignored = failed = 0
    for event_id, attempts, max_attempts in snapshot:
        outcome = _reconcile_claimed_row(
            event_id, attempts=attempts, max_attempts=max_attempts
        )
        if outcome == StripeInboundStatus.PROCESSED:
            processed += 1
        elif outcome == StripeInboundStatus.IGNORED:
            ignored += 1
        else:
            failed += 1

    with SessionLocal() as db:
        backlog = inbound_service.backlog_depth(db)

    result = {
        "claimed": len(snapshot),
        "processed": processed,
        "ignored": ignored,
        "failed": failed,
        "leases_reaped": reaped,
        "backlog_remaining": backlog,
    }

    if failed:
        logger.warning("billing.reconcile_partial", extra=result)
    else:
        logger.info("billing.reconcile_complete", extra=result)
    return result


def _reconcile_one_by_id(event_id: uuid.UUID) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(StripeInboundEvent, event_id)
        if row is None:
            raise ValueError(f"No inbound event {event_id}.")
        attempts, max_attempts = row.attempts, row.max_attempts

    outcome = _reconcile_claimed_row(
        event_id, attempts=attempts, max_attempts=max_attempts
    )
    return {
        "inbound_event_id": str(event_id),
        "status": outcome.value,
        "claimed": 1,
    }


def _reconcile_claimed_row(
    event_id: uuid.UUID, *, attempts: int, max_attempts: int
) -> StripeInboundStatus:
    """Reconcile one row and mark it, each in its own transaction.

    The marking is a separate session from the work deliberately: if the
    reconcile transaction rolls back, the mark must not roll back with it, or
    a row that failed for a deterministic reason would be reclaimed and fail
    identically until it burned its attempt ceiling with no record of why.
    """
    with system_principal(
        job_name="jobs.billing.reconcile", job_id=event_id
    ):
        try:
            with SessionLocal() as db:
                with db.begin():
                    row = db.get(StripeInboundEvent, event_id)
                    if row is None:
                        raise ValueError(f"Inbound event {event_id} vanished.")

                    event = reconcile_service.event_from_row(row)
                    outcome = reconcile_service.reconcile_event(db, event)

                    if outcome.organization_id is not None:
                        inbound_service.attach_organization(
                            db,
                            event_id,
                            organization_id=outcome.organization_id,
                        )

                    if outcome.handled:
                        inbound_service.mark_processed(
                            db, event_id, result=_jsonable(outcome.detail)
                        )
                        status = StripeInboundStatus.PROCESSED
                    else:
                        inbound_service.mark_ignored(
                            db,
                            event_id,
                            reason=outcome.ignored_reason or "unhandled",
                            detail=_short(outcome.detail),
                        )
                        status = StripeInboundStatus.IGNORED

            logger.info(
                "billing.inbound_reconciled",
                extra={
                    "stripe_inbound_event_id": str(event_id),
                    "status": status.value,
                },
            )
            return status

        except (ReconcileRefused, stripe_gateway.StripePermanentError) as exc:
            # Deterministic. Retrying eight times changes nothing except how
            # long it takes somebody to notice, so it goes straight to the
            # ceiling.
            with SessionLocal() as db:
                with db.begin():
                    inbound_service.mark_failed(
                        db,
                        event_id,
                        attempts=max_attempts,
                        max_attempts=max_attempts,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            logger.error(
                "billing.inbound_refused",
                extra={
                    "stripe_inbound_event_id": str(event_id),
                    "error": str(exc),
                },
            )
            return StripeInboundStatus.DEAD

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "billing.inbound_failed",
                extra={"stripe_inbound_event_id": str(event_id)},
            )
            with SessionLocal() as db:
                with db.begin():
                    status = inbound_service.mark_failed(
                        db,
                        event_id,
                        attempts=attempts,
                        max_attempts=max_attempts,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            return status


def _jsonable(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, dict):
            out[key] = _jsonable(value)
        else:
            out[key] = str(value)
    return out


def _short(detail: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in (detail or {}).items())[:500]


# ============================================================================
# billing.seat_sync / billing.seat_drift
# ============================================================================


def handle_billing_seat_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Assert Stripe's seat count against the view.

    `{"organization_id": "..."}` for one tenant; `{}` to sweep every drifting
    one. The sweep is bounded by `limit` because a sweep that can issue an
    unbounded number of Stripe writes is an incident waiting for its first bad
    deploy.
    """
    organization_id = _uuid(payload, "organization_id")
    reason = str(payload.get("reason") or "seat_sync")
    limit = _int(payload, "limit", 100)

    with SessionLocal() as db:
        with system_principal(job_name="jobs.billing.seat_sync"):
            if organization_id is not None:
                targets = [organization_id]
            else:
                targets = list(
                    seat_service.organizations_needing_sync(db, limit=limit)
                )

    results: list[dict[str, Any]] = []
    for target in targets:
        with SessionLocal() as db:
            with system_principal(job_name="jobs.billing.seat_sync"):
                with db.begin():
                    results.append(
                        seat_service.sync_seats(
                            db, organization_id=target, reason=reason
                        )
                    )

    outcome = {
        "targets": len(targets),
        "synced": sum(1 for r in results if r.get("outcome") == "SYNCED"),
        "in_sync": sum(1 for r in results if r.get("outcome") == "IN_SYNC"),
        "results": results[:50],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("billing.seat_sync_complete", extra=outcome)
    return outcome


def handle_billing_seat_drift(payload: dict[str, Any]) -> dict[str, Any]:
    """Detect and report. Corrects nothing.

    This is the gate, not the alert. Drift is a symptom whose cause is always
    upstream — a failed modify, a membership change that did not emit, a lost
    reconcile race — and a job that quietly corrects it removes the only
    evidence that the cause exists.
    """
    limit = _int(payload, "limit", 1000)

    with SessionLocal() as db:
        with system_principal(job_name="jobs.billing.seat_drift"):
            with db.begin():
                drifts = seat_service.report_drift(db, limit=limit)

    outcome = {
        "checked_limit": limit,
        "drifting": len(drifts),
        "under_billed": sum(1 for d in drifts if d.direction == "UNDER_BILLED"),
        "over_billed": sum(1 for d in drifts if d.direction == "OVER_BILLED"),
        "detail": [d.as_dict() for d in drifts[:50]],
    }
    if drifts:
        logger.error("billing.seat_drift_detected", extra=outcome)
    return outcome


# ============================================================================
# Enqueue helpers
# ============================================================================


def enqueue_reconcile(db, *, available_at: Optional[datetime] = None):
    from app.services import job_service

    return job_service.enqueue(
        db,
        job_type=RECONCILE_JOB_TYPE,
        payload={},
        max_attempts=3,
        available_at=available_at,
    )


def enqueue_seat_sync(
    db,
    *,
    organization_id: uuid.UUID,
    reason: str = "seat_sync",
    available_at: Optional[datetime] = None,
):
    from app.services import job_service

    return job_service.enqueue(
        db,
        job_type=SEAT_SYNC_JOB_TYPE,
        payload={"organization_id": str(organization_id), "reason": reason},
        organization_id=organization_id,
        max_attempts=5,
        available_at=available_at,
    )


__all__ = [
    "RECONCILE_JOB_TYPE",
    "SEAT_DRIFT_JOB_TYPE",
    "SEAT_SYNC_JOB_TYPE",
    "enqueue_reconcile",
    "enqueue_seat_sync",
    "handle_billing_reconcile",
    "handle_billing_seat_drift",
    "handle_billing_seat_sync",
]