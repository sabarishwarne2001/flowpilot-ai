"""`usage.limit_alerts` — tell owners before and when a monthly plan limit runs out (N-062).

LIGHT profile, hourly. For every active organization that used a metered allowance this
month (`plan_admission.ALLOWANCE_METERS`: document uploads, OCR pages, assistant messages),
the allowance is read exactly as the refusals read it (`plan_admission.allowance`), and:

* at 80% the owners get a **Usage warning** (in the app, and by email unless they turned
  usage warnings off);
* at 100% they get a **Billing** notice (always emailed): what stops, until when, and that
  changing the plan lifts it.

Owners only: they are the people who can change the plan (`plan_admission.plan_contacts`);
a member who hits the limit already sees the refusal, which names them.

Each alert is a row in `usage_limit_alerts` (organization, meter, level, limit, period),
inserted before the notice in the same transaction, so it goes out once per period however
often the sweep runs and on however many workers; a plan change is a new limit and re-arms
both. An organization that draws on a shared pool but used nothing itself this month is not
looked at (its pool partners that did are).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger("app.workers.handlers.usage_alerts")

JOB_TYPE = "usage.limit_alerts"

#: How each meter is named in a sentence: (plural noun, singular noun for "limit").
METER_WORDS: dict[str, tuple[str, str]] = {
    "document.upload": ("document uploads", "document upload"),
    "ocr.page": ("pages read from scans", "scanned page"),
    "assistant.message": ("assistant messages", "assistant message"),
}


def _words(key: str) -> tuple[str, str]:
    return METER_WORDS.get(key, (key, key))


def _day(value: Any) -> str:
    return value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value)


def _candidates(db: Any, *, since: datetime) -> list[Any]:
    from app.models.organization import Organization, OrganizationStatus
    from app.models.usage_event import UsageEvent
    from app.services.plan_admission import ALLOWANCE_METERS

    used = (
        select(UsageEvent.organization_id)
        .where(UsageEvent.event_type.in_(ALLOWANCE_METERS), UsageEvent.occurred_at >= since)
        .distinct()
    )
    return list(
        db.execute(
            select(Organization.id).where(
                Organization.id.in_(used), Organization.status == OrganizationStatus.ACTIVE
            )
        ).scalars()
    )


def _claim(db: Any, *, organization_id: Any, meter: dict[str, Any], level: str, period_start: datetime) -> bool:
    from app.models.notification_preference import UsageLimitAlert

    table = UsageLimitAlert.__table__
    inserted = db.execute(
        pg_insert(table)
        .values(
            organization_id=organization_id,
            limit_key=meter["key"],
            level=level,
            ceiling=int(meter["limit"]),
            period_start=period_start,
        )
        .on_conflict_do_nothing(constraint="uq_usage_limit_alerts_once_per_period")
        .returning(table.c.id)
    ).scalar_one_or_none()
    return inserted is not None


def _notify(db: Any, *, organization: Any, meter: dict[str, Any], level: str) -> None:
    from app.models.notification import NotificationPriority, NotificationType
    from app.models.organization import OrganizationRole
    from app.services import notification_preferences_service as email_choices
    from app.services import organization_notification_service as notices

    plural, singular = _words(meter["key"])
    resets = _day(meter["resets_at"])
    used, limit = int(meter["used"]), int(meter["limit"])
    if level == "NEAR":
        title = f"You have used 80% of this month's {plural}"
        message = (
            f"{organization.name} has used {used} of its {limit} {plural} this month. "
            f"The allowance resets on {resets} (UTC). Changing the plan raises it now."
        )
        category, priority = email_choices.USAGE, NotificationPriority.WARNING
    else:
        title = f"You have reached this month's {singular} limit"
        consequence = (
            f"New {plural} are refused until the allowance resets on {resets} (UTC)."
            if meter["hard_stop"] or meter["overage_policy"] == "REFUSE"
            else f"Anything above it this month is billed at your plan's overage rate; it resets on {resets} (UTC)."
        )
        message = (
            f"{organization.name} has used {used} of {limit} {plural} this month. "
            f"{consequence} Changing the plan raises the limit at once. Nothing has been deleted."
        )
        category, priority = email_choices.BILLING, NotificationPriority.WARNING
    notices.emit_to_roles(
        db,
        organization_id=organization.id,
        roles=(OrganizationRole.OWNER,),
        title=title,
        message=message,
        notification_type=NotificationType.SYSTEM,
        priority=priority,
        category=category,
        action="billing",
    )


def run_limit_alerts(db: Any, *, now: Optional[datetime] = None) -> dict[str, int]:
    """Send what is due; returns how many organizations were read and alerts sent. Commits per organization."""
    from app.models.organization import Organization
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import plan_admission
    from app.services import spend_control_service as spend

    moment = now or datetime.now(timezone.utc)
    period_start = spend.period_start(SpendLimitPeriod.MONTH, now=moment)
    organization_ids = _candidates(db, since=period_start)
    sent = 0
    for organization_id in organization_ids:
        try:
            organization = db.get(Organization, organization_id)
            if organization is None:
                continue
            for meter in plan_admission.allowance(db, organization_id=organization_id)["meters"]:
                state = meter["state"]
                if state == "OK":
                    continue
                level = "NEAR" if state == "NEAR" else "REACHED"
                if _claim(db, organization_id=organization_id, meter=meter, level=level, period_start=period_start):
                    _notify(db, organization=organization, meter=meter, level=level)
                    sent += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one organization must not stop the sweep
            db.rollback()
            logger.exception("usage.limit_alerts_failed", extra={"organization_id": str(organization_id)})
    return {"organizations": len(organization_ids), "sent": sent}


def handle_limit_alerts(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db import session as session_module

    with session_module.SessionLocal() as db:
        result = run_limit_alerts(db)
    if result["sent"]:
        logger.info("usage.limit_alerts_sent", extra=result)
    return result


__all__ = ["JOB_TYPE", "handle_limit_alerts", "run_limit_alerts"]
