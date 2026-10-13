"""Campaign session 4 (§C.4) — tell whoever runs the deployment when something breaks.

Dead jobs were visible at /admin/jobs (F-270) and the uptime heartbeat says when the site is down
(N-017), but nothing *told* the owner when background work failed for good, a payment event
could not be applied, the API started answering with errors, or a customer ended up with two live
subscriptions. This module records each such condition as an `operator_alerts` row and emails the
addresses in `OPERATOR_ALERT_EMAILS` through the platform relay:

* **Dead jobs** (per job type) and **dead gateway events** (per gateway and event type): any that
  died since the owner was last told.
* **Server errors**: `OPERATOR_ALERT_5XX_THRESHOLD` or more 5xx answers in five minutes, summed
  across API processes (`server_error_minutes`).
* **Duplicate subscription**: a gateway subscription that arrives while the organization already
  has a live one (an old checkout tab). It is not recorded; the customer is being charged for it;
  RUNBOOK §9.8 is the refund playbook.

The `ops.alerts` sweep (every five minutes) scans and sends; a duplicate subscription also queues
the sweep at once. An alert that keeps happening is emailed again at most every
`OPERATOR_ALERT_REPEAT_MINUTES`, with the count, so a storm is one email, not hundreds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operator_alert import OperatorAlert

logger = logging.getLogger("app.services.operator_alerts")

JOB_TYPE = "ops.alerts"
#: Dead work counted in an alert's figure.
DEAD_WINDOW = timedelta(hours=24)
#: The window a burst of server errors is measured over.
ERROR_WINDOW = timedelta(minutes=5)
#: How long per-minute error counts are kept.
ERROR_RETENTION = timedelta(days=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    db: Session,
    *,
    kind: str,
    dedupe_key: str,
    subject: str,
    body: str,
    seen_at: Optional[datetime] = None,
    occurrences: Optional[int] = None,
) -> None:
    """Create or refresh one alert. `occurrences` sets the figure; omitted, it counts one more."""
    moment = seen_at or _now()
    table = OperatorAlert.__table__
    stmt = pg_insert(table).values(
        kind=kind, dedupe_key=dedupe_key, subject=subject[:500], body=body,
        occurrences=max(1, occurrences or 1), first_seen_at=moment, last_seen_at=moment,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.dedupe_key],
        set_={
            "subject": stmt.excluded.subject,
            "body": stmt.excluded.body,
            "occurrences": (
                stmt.excluded.occurrences if occurrences is not None else table.c.occurrences + 1
            ),
            "last_seen_at": func.greatest(table.c.last_seen_at, stmt.excluded.last_seen_at),
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)


def duplicate_subscription(
    db: Session,
    *,
    organization_id: Any,
    gateway: str,
    customer_id: str,
    recorded_subscription_id: str,
    duplicate_subscription_id: str,
    duplicate_status: str,
) -> None:
    """A second live gateway subscription for one organization: alert now (not at the next sweep)."""
    from app.models.organization import Organization

    organization = db.get(Organization, organization_id)
    who = f"{organization.name} ({organization.slug})" if organization is not None else str(organization_id)
    record(
        db,
        kind="DUPLICATE_SUBSCRIPTION",
        dedupe_key=f"duplicate_subscription:{gateway}:{duplicate_subscription_id}",
        subject=f"Customer charged twice: {who} has two live {gateway.title()} subscriptions",
        body=(
            f"{who} already has a live subscription, {recorded_subscription_id}, and {gateway.title()} "
            f"reports a second one, {duplicate_subscription_id} (status {duplicate_status}, customer "
            f"{customer_id}). FlowPilot does not record the second (one live subscription per "
            "organization), but the customer is being charged for it. In the payment dashboard: "
            f"cancel {duplicate_subscription_id} now and refund what it charged, then tell the "
            "customer. Step by step: RUNBOOK section 9.8."
        ),
    )
    _queue_sweep(db)


def _queue_sweep(db: Session) -> None:
    from app.services import job_service

    if JOB_TYPE not in job_service.JOB_HANDLERS:
        # A script or test driving the reconciler directly may not have registered the worker's
        # handlers, and enqueue refuses a job type without one (the outbox does the same).
        from app.workers.handlers import register_all

        register_all()
    minute = _now().replace(second=0, microsecond=0)
    try:
        job_service.enqueue(
            db, job_type=JOB_TYPE, organization_id=None, payload={},
            idempotency_key=f"{JOB_TYPE}:now:{minute.isoformat()}",
        )
    except Exception:  # noqa: BLE001 - the five-minute sweep still sends it
        logger.warning("ops.alert_sweep_not_queued", exc_info=True)


def scan(db: Session, *, now: Optional[datetime] = None) -> int:
    """Record what is wrong right now. Returns how many alerts were refreshed."""
    moment = now or _now()
    refreshed = 0

    dead_jobs = db.execute(text(
        "SELECT job_type, count(*), max(updated_at), "
        "(array_agg(left(coalesce(last_error, ''), 300) ORDER BY updated_at DESC))[1] "
        "FROM jobs WHERE status = 'DEAD' AND updated_at > :since GROUP BY job_type"
    ), {"since": moment - DEAD_WINDOW}).all()
    for job_type, count, latest, error in dead_jobs:
        record(
            db, kind="DEAD_JOBS", dedupe_key=f"dead_jobs:{job_type}", seen_at=latest, occurrences=int(count),
            subject=f"{count} background {'job' if count == 1 else 'jobs'} failed for good: {job_type}",
            body=(
                f"{count} {job_type} {'job' if count == 1 else 'jobs'} failed every attempt in the last "
                f"24 hours and will not run again on their own. Latest error: {error or '(none recorded)'}. "
                "Open Platform admin, Background jobs, to see who they were for, fix the cause, then "
                "retry them there."
            ),
        )
        refreshed += 1

    dead_events = db.execute(text(
        "SELECT gateway, event_type, count(*), max(processed_at), "
        "(array_agg(left(coalesce(last_error, ''), 300) ORDER BY processed_at DESC))[1] "
        "FROM stripe_inbound_events WHERE status = 'DEAD' AND processed_at > :since "
        "GROUP BY gateway, event_type"
    ), {"since": moment - DEAD_WINDOW}).all()
    for gateway, event_type, count, latest, error in dead_events:
        gateway_name = (gateway or "STRIPE").title()
        record(
            db, kind="DEAD_GATEWAY_EVENTS", dedupe_key=f"dead_gateway_events:{gateway}:{event_type}",
            seen_at=latest, occurrences=int(count),
            subject=f"{count} {gateway_name} {event_type} {'event' if count == 1 else 'events'} could not be applied",
            body=(
                f"{count} {gateway_name} {event_type} {'event' if count == 1 else 'events'} failed every "
                "attempt in the last 24 hours: a subscription, invoice or refund may be out of step with "
                f"the payment dashboard. Latest error: {error or '(none recorded)'}. Compare the "
                "organization's plan in FlowPilot with the dashboard, and see RUNBOOK section 9.7."
            ),
        )
        refreshed += 1

    errors = int(db.execute(text(
        "SELECT COALESCE(sum(count), 0) FROM server_error_minutes WHERE minute >= :since"
    ), {"since": moment - ERROR_WINDOW}).scalar_one())
    threshold = int(settings.OPERATOR_ALERT_5XX_THRESHOLD)
    if errors >= threshold > 0:
        record(
            db, kind="SERVER_ERRORS", dedupe_key="server_errors", seen_at=moment, occurrences=errors,
            subject=f"The API answered {errors} requests with a server error in five minutes",
            body=(
                f"{errors} requests failed with a server error (5xx) in the last five minutes, across "
                f"every API process (the alert fires at {threshold}). Look at the API log for "
                "Traceback lines (RUNBOOK section 9.7: `$COMPOSE logs --tail 200 web`)."
            ),
        )
        refreshed += 1

    db.execute(text("DELETE FROM server_error_minutes WHERE minute < :old"),
               {"old": moment - ERROR_RETENTION})
    return refreshed


def due(db: Session, *, now: Optional[datetime] = None) -> list[OperatorAlert]:
    """Alerts with news the owner has not been emailed about, outside the repeat window."""
    moment = now or _now()
    repeat = timedelta(minutes=max(1, int(settings.OPERATOR_ALERT_REPEAT_MINUTES)))
    return list(db.execute(
        select(OperatorAlert)
        .where(
            (OperatorAlert.last_emailed_at.is_(None))
            | ((OperatorAlert.last_seen_at > OperatorAlert.last_emailed_at)
               & (OperatorAlert.last_emailed_at <= moment - repeat))
        )
        .order_by(OperatorAlert.first_seen_at)
    ).scalars())


def _send(*, recipient: str, subject: str, html_body: str, text_body: str) -> bool:
    from app.core.platform_email import send_platform_email

    ok, _detail = send_platform_email(
        recipient=recipient, subject=subject, html_body=html_body, text_body=text_body
    )
    return ok


def send_due(db: Session, *, now: Optional[datetime] = None) -> int:
    """Email every due alert to every recipient; returns how many alerts went out."""
    from app.templates.emails.operator_alert import render_operator_alert

    moment = now or _now()
    recipients = settings.operator_alert_recipients
    pending = due(db, now=moment)
    if not pending:
        return 0
    if not recipients:
        logger.warning("ops.alert_unrouted", extra={"alerts": len(pending)})
        return 0

    console_url = settings.FRONTEND_URL.rstrip("/") + "/admin/jobs"
    sent = 0
    for alert in pending:
        subject, html_body, text_body = render_operator_alert(
            subject=alert.subject,
            body=alert.body,
            occurrences=int(alert.occurrences),
            first_seen_at=alert.first_seen_at,
            last_seen_at=alert.last_seen_at,
            console_url=console_url,
            brand_name=settings.PLATFORM_SMTP_FROM_NAME or "FlowPilot",
        )
        delivered = False
        for recipient in recipients:
            try:
                delivered = _send(recipient=recipient, subject=subject, html_body=html_body,
                                  text_body=text_body) or delivered
            except Exception:  # noqa: BLE001 - one bad address never stops the others
                logger.warning("ops.alert_send_failed", extra={"alert": alert.dedupe_key}, exc_info=True)
        if delivered:
            alert.last_emailed_at = moment
            alert.emailed_count = int(alert.emailed_count or 0) + 1
            sent += 1
            logger.info("ops.alert_sent", extra={"alert": alert.dedupe_key, "kind": alert.kind})
    db.flush()
    return sent


__all__ = ["JOB_TYPE", "due", "duplicate_subscription", "record", "scan", "send_due"]
