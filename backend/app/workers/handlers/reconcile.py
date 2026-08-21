"""ARCH-14 Step 5 — the `usage.reconcile` job handler."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.principal import system_principal
from app.db.session import SessionLocal
from app.services import rollup_service
from app.services.reconciliation import (
    ReconciliationRefused,
    StatementSourceError,
    reconcile_provider,
    registered_sources,
)

logger = logging.getLogger("app.workers.handlers.reconcile")

RECONCILE_JOB_TYPE = "usage.reconcile"


def _instant(payload: dict[str, Any], key: str) -> Optional[datetime]:
    raw = payload.get(key)
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _previous_month(now: datetime) -> tuple[datetime, datetime]:
    this_month = rollup_service.month_bucket(now)
    previous = rollup_service.month_bucket(this_month - timedelta(days=1))
    return previous, this_month


def handle_usage_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip().lower()
    if not provider:
        raise ValueError(
            "usage.reconcile payload requires 'provider'. Known: "
            f"{sorted(registered_sources())}"
        )

    now = _instant(payload, "now") or datetime.now(timezone.utc)
    period_start = _instant(payload, "period_start")
    period_end = _instant(payload, "period_end")
    if period_start is None or period_end is None:
        period_start, period_end = _previous_month(now)

    min_age = int(getattr(settings, "RECONCILE_MIN_AGE_DAYS", 2))
    eligible_at = period_end + timedelta(days=min_age)

    if now < eligible_at:
        outcome = {
            "provider": provider,
            "period_start": period_start.isoformat(),
            "status": "REFUSED",
            "eligible_at": eligible_at.isoformat(),
            "reason": f"period is younger than T+{min_age} days",
        }
        logger.info("reconciliation.job_deferred", extra=outcome)
        _reschedule(
            provider=provider,
            period_start=period_start,
            period_end=period_end,
            available_at=eligible_at,
        )
        return outcome

    fetch_options = payload.get("fetch_options") or {}

    with SessionLocal() as db:
        with system_principal(job_name="jobs.usage.reconcile"):
            try:
                with db.begin():
                    run = reconcile_provider(
                        db,
                        provider=provider,
                        period_start=period_start,
                        period_end=period_end,
                        fetch_options=fetch_options,
                        now=now,
                    )
                    outcome = {
                        "provider": provider,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "status": run.status,
                        "reconciliation_run_id": str(run.id),
                        "ledger_cost_micros": run.ledger_cost_micros,
                        "statement_cost_micros": run.statement_cost_micros,
                        "drift_micros": run.drift_micros,
                        "drift_bps": str(run.drift_bps),
                        "findings": run.findings_count,
                        "alert_raised": run.alert_raised,
                        "attribution": run.attribution,
                    }
            except ReconciliationRefused as exc:
                outcome = {
                    "provider": provider,
                    "period_start": period_start.isoformat(),
                    "status": "REFUSED",
                    "reason": str(exc),
                }
                logger.info("reconciliation.job_refused", extra=outcome)
                _reschedule(
                    provider=provider,
                    period_start=period_start,
                    period_end=period_end,
                    available_at=eligible_at,
                )
                return outcome
            except StatementSourceError:
                logger.exception(
                    "reconciliation.statement_unavailable",
                    extra={
                        "provider": provider,
                        "period_start": period_start.isoformat(),
                    },
                )
                raise

    if outcome.get("alert_raised"):
        logger.critical("reconciliation.alert", extra=outcome)
    else:
        logger.info("reconciliation.job_complete", extra=outcome)
    return outcome


def _reschedule(
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    available_at: datetime,
) -> None:
    from app.services import job_service

    with SessionLocal() as db:
        with db.begin():
            job_service.enqueue(
                db,
                job_type=RECONCILE_JOB_TYPE,
                payload={
                    "provider": provider,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                },
                max_attempts=3,
                available_at=available_at,
                idempotency_key=(
                    f"reconcile:{provider}:{period_start.date().isoformat()}"
                ),
            )


def enqueue_reconcile(
    db,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    fetch_options: Optional[dict[str, Any]] = None,
    available_at: Optional[datetime] = None,
):
    from app.services import job_service

    return job_service.enqueue(
        db,
        job_type=RECONCILE_JOB_TYPE,
        payload={
            "provider": provider,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "fetch_options": fetch_options or {},
        },
        max_attempts=3,
        available_at=available_at,
        idempotency_key=f"reconcile:{provider}:{period_start.date().isoformat()}",
    )


__all__ = [
    "RECONCILE_JOB_TYPE",
    "enqueue_reconcile",
    "handle_usage_reconcile",
]