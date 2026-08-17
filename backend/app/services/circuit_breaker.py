"""ARCH-09 Step 7b — the endpoint circuit breaker."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

logger = logging.getLogger(__name__)

CIRCUIT_BREAKER_THRESHOLD: int = 10
CIRCUIT_BREAKER_MIN_SPAN: timedelta = timedelta(hours=1)
SSRF_IMMEDIATE_DISABLE: bool = True
BREAKER_REASON_PREFIX = "Circuit breaker: "


class BreakerOutcome:
    """What the breaker decided about this failure."""

    __slots__ = ("tripped", "consecutive_failures", "reason")

    def __init__(
        self, *, tripped: bool, consecutive_failures: int, reason: Optional[str] = None
    ) -> None:
        self.tripped = tripped
        self.consecutive_failures = consecutive_failures
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BreakerOutcome tripped={self.tripped} "
            f"failures={self.consecutive_failures}>"
        )


def record_success(db: Session, endpoint_id: uuid.UUID) -> None:
    """Reset the streak. Flushes; does not commit."""
    db.execute(
        update(WebhookEndpoint)
        .where(WebhookEndpoint.id == endpoint_id)
        .values(
            consecutive_failures=0,
            first_failure_at=None,
            last_success_at=func.now(),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()


def record_failure(
    db: Session,
    endpoint_id: uuid.UUID,
    *,
    error: Optional[str] = None,
    ssrf_refused: bool = False,
) -> BreakerOutcome:
    """Increment the streak and trip the breaker if both conditions are met."""
    table = WebhookEndpoint.__table__

    row = db.execute(
        update(table)
        .where(table.c.id == endpoint_id)
        .values(
            consecutive_failures=table.c.consecutive_failures + 1,
            first_failure_at=func.coalesce(table.c.first_failure_at, func.now()),
            last_failure_at=func.now(),
            updated_at=func.now(),
        )
        .returning(
            table.c.consecutive_failures,
            table.c.first_failure_at,
            table.c.status,
            table.c.organization_id,
        )
        .execution_options(synchronize_session=False)
    ).first()

    if row is None:
        return BreakerOutcome(tripped=False, consecutive_failures=0)

    failures, first_failure_at, status, organization_id = row

    if status == WebhookEndpointStatus.DISABLED.value:
        db.flush()
        return BreakerOutcome(tripped=False, consecutive_failures=failures)

    reason: Optional[str] = None

    if ssrf_refused and SSRF_IMMEDIATE_DISABLE:
        reason = (
            f"{BREAKER_REASON_PREFIX}the endpoint URL resolved to an address "
            "FlowPilot will never deliver to (loopback, private, link-local, "
            "or cloud metadata). This is a configuration error and will not "
            "resolve on retry. Correct the DNS record or the URL, then "
            "re-enable."
        )
    elif failures >= CIRCUIT_BREAKER_THRESHOLD and first_failure_at is not None:
        span = datetime.now(timezone.utc) - _as_aware(first_failure_at)
        if span >= CIRCUIT_BREAKER_MIN_SPAN:
            hours = span.total_seconds() / 3600
            reason = (
                f"{BREAKER_REASON_PREFIX}{failures} consecutive failures over "
                f"{hours:.1f} hours. Deliveries are paused until an "
                "administrator re-enables this endpoint. Queued deliveries "
                "will not be retried while it is disabled."
            )
        else:
            logger.info(
                "webhook.breaker.threshold_without_span",
                extra={
                    "webhook_endpoint_id": str(endpoint_id),
                    "consecutive_failures": failures,
                    "span_seconds": int(span.total_seconds()),
                },
            )

    if reason is None:
        db.flush()
        return BreakerOutcome(tripped=False, consecutive_failures=failures)

    _trip(
        db,
        endpoint_id=endpoint_id,
        organization_id=organization_id,
        reason=reason,
        consecutive_failures=failures,
    )
    return BreakerOutcome(
        tripped=True, consecutive_failures=failures, reason=reason
    )


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _trip(
    db: Session,
    *,
    endpoint_id: uuid.UUID,
    organization_id: uuid.UUID,
    reason: str,
    consecutive_failures: int,
) -> None:
    from app.models.webhook_endpoint import WebhookEndpoint as _WE

    endpoint = db.execute(
        select(_WE).where(_WE.id == endpoint_id)
    ).scalar_one_or_none()
    if endpoint is None:
        return

    from app.services.webhook_service import disable_endpoint

    disable_endpoint(db, endpoint, disabled_by_user_id=None, reason=reason)
    endpoint.auto_disabled = True
    db.flush()

    logger.warning(
        "webhook.breaker.tripped",
        extra={
            "webhook_endpoint_id": str(endpoint_id),
            "organization_id": str(organization_id),
            "consecutive_failures": consecutive_failures,
        },
    )

    _write_audit_entry(
        db,
        endpoint=endpoint,
        organization_id=organization_id,
        reason=reason,
        consecutive_failures=consecutive_failures,
    )
    _write_notification(
        db,
        endpoint=endpoint,
        organization_id=organization_id,
        reason=reason,
    )


def _write_audit_entry(
    db: Session,
    *,
    endpoint,
    organization_id: uuid.UUID,
    reason: str,
    consecutive_failures: int,
) -> None:
    try:
        from app.services import audit_service
    except ImportError:  # pragma: no cover
        logger.error("audit_service unavailable; breaker trip NOT audited")
        return

    try:
        audit_service.record(
            db,
            organization_id=organization_id,
            action="WEBHOOK_ENDPOINT_AUTO_DISABLED",
            resource_type="WEBHOOK_ENDPOINT",
            resource_id=endpoint.id,
            outcome="ALLOWED",
            details={
                "principal": "SYSTEM",
                "reason": reason,
                "consecutive_failures": consecutive_failures,
                "url": endpoint.url,
                "auto_disabled": True,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "webhook.breaker.audit_failed",
            extra={"webhook_endpoint_id": str(endpoint.id)},
        )


def _write_notification(
    db: Session, *, endpoint, organization_id: uuid.UUID, reason: str
) -> None:
    logger.warning(
        "webhook.breaker.notification_not_sent",
        extra={
            "webhook_endpoint_id": str(endpoint.id),
            "organization_id": str(organization_id),
            "detail": "ARCH-06 notification service not wired",
        },
    )


def reset_breaker(db: Session, endpoint) -> None:
    endpoint.consecutive_failures = 0
    endpoint.first_failure_at = None
    endpoint.auto_disabled = False
    db.flush()