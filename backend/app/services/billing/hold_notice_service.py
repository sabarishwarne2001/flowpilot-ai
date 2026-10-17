"""Notices while a Dodo renewal is on hold (website and payments session, C1).

A declined Dodo renewal puts the subscription `on_hold`; Servabo keeps full access through a grace
window (`grace_ends_at`, D-11) while Dodo retries the card, and reads the organization as
read-only once the window has closed (`dunning_service.access_state`), with or without a further
webhook. The owner hears "Payment failed" from the reconciler when the hold starts. This module,
run by the `billing.hold_notices` job, says the rest by the clock, as the Stripe path's dunning
steps do:

    7 days or less left    "Payment still outstanding"            REMINDER
    2 days or less left    "Final notice before access changes"  FINAL
    the window has closed  "Organization is read-only"           READ_ONLY

Only the latest step that is true is sent (a job that was down does not send three notices at
once), each once per hold: the row records the step and the hold's `grace_ends_at`, so a later
hold, with its own window, starts again. The notices go to owners and Billing members by email and
in the app (`emit_billing`, N-062).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing_account import BillingAccount
from app.models.subscription import Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)

REMINDER_BEFORE = timedelta(days=7)
FINAL_BEFORE = timedelta(days=2)
STEPS = ("REMINDER", "FINAL", "READ_ONLY")


def _step_due(grace_ends_at: datetime, now: datetime) -> Optional[str]:
    left = grace_ends_at - now
    if left <= timedelta(0):
        return "READ_ONLY"
    if left <= FINAL_BEFORE:
        return "FINAL"
    if left <= REMINDER_BEFORE:
        return "REMINDER"
    return None


def _message(step: str, grace_ends_at: datetime) -> tuple[str, str]:
    on = grace_ends_at.strftime("%d %b %Y %H:%M UTC")
    if step == "REMINDER":
        return ("Payment still outstanding",
                "Dodo Payments could not take the renewal payment and is still retrying the card. Your "
                f"organization keeps full access until {on}. Update the payment method in Billing to "
                "avoid interruption.")
    if step == "FINAL":
        return ("Final notice before access changes",
                f"The renewal payment is still outstanding. On {on} the organization becomes read-only: "
                "nothing is deleted, everything stays readable and export remains available. Update "
                "the payment method in Billing now to keep full access.")
    return ("Organization is read-only",
            "The renewal payment is still outstanding, so the organization is now read-only. Nothing "
            "has been deleted, everything stays readable and export remains available. Updating the "
            "payment method in Billing restores full access.")


def send_due(db: Session, *, now: Optional[datetime] = None, limit: int = 500) -> list[dict[str, Any]]:
    """Send each held Dodo subscription the notice its grace window now calls for, once."""
    from app.models.notification import NotificationPriority
    from app.services import organization_notification_service as notices

    moment = now or datetime.now(timezone.utc)
    held = db.execute(
        select(Subscription, BillingAccount.organization_id)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(Subscription.gateway == "DODO",
               Subscription.status.in_((SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID)),
               Subscription.grace_ends_at.is_not(None),
               Subscription.grace_ends_at <= moment + REMINDER_BEFORE)
        .order_by(Subscription.grace_ends_at)
        .limit(limit)
        .with_for_update(of=Subscription, skip_locked=True)
    ).all()
    sent: list[dict[str, Any]] = []
    for subscription, organization_id in held:
        step = _step_due(subscription.grace_ends_at, moment)
        if step is None:
            continue
        already = (subscription.hold_notice_step
                   if subscription.hold_notice_grace_ends_at == subscription.grace_ends_at else None)
        if already is not None and STEPS.index(already) >= STEPS.index(step):
            continue
        title, message = _message(step, subscription.grace_ends_at)
        notices.emit_billing(db, organization_id=organization_id, title=title, message=message,
                             priority=NotificationPriority.INFO if step == "REMINDER" else NotificationPriority.WARNING)
        subscription.hold_notice_step = step
        subscription.hold_notice_grace_ends_at = subscription.grace_ends_at
        db.flush([subscription])
        sent.append({"subscription_id": subscription.id, "organization_id": organization_id, "step": step})
        logger.info("billing.hold_notice_sent", extra={"subscription_id": str(subscription.id), "step": step})
    return sent
