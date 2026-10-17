"""Changing plan on a live Dodo subscription (website and payments session, C6).

Rules (docs/billing/DODO.md, "Plan changes"):

* The owner sees the amount charged now and when the change takes effect before confirming
  (`preview`), and the confirmation must carry that amount (`apply` refuses another one: prices or
  the period moved since).
* An **upgrade** (a dearer plan) takes effect at once. Dodo charges `prorated_immediately`: the days
  left in the period, at the price difference, so no day is paid twice; the renewal date does not
  move. `on_payment_failure: prevent_change` keeps the old plan if that charge fails.
* A **downgrade** takes effect at the next renewal, so the paid period keeps the plan paid for. It
  is checked first (N-003: seats in use and workspaces must fit the new plan; data over a storage
  ceiling is reported), then scheduled on the subscription and made at Dodo with `do_not_bill`
  shortly before the renewal (`apply_due`, every 15 minutes), so the renewal is billed at the new
  price. It can be withdrawn until then.
* Seats alone change through the seat flow (N-041), not here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.plan_limits import SEATS_LIMIT, WORKSPACES_LIMIT
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.billing_account import BillingAccount
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.subscription import Subscription

logger = logging.getLogger("app.services.billing.plan_change")

UPGRADE_MODE = "prorated_immediately"
DOWNGRADE_MODE = "do_not_bill"
#: A scheduled downgrade is made at Dodo this long before the renewal (the sweep runs every 15 minutes).
APPLY_AHEAD = timedelta(hours=1)
_GIB = Decimal(1024**3)


class PlanChangeError(Exception):
    """Refused, with a sentence for the owner. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, *, status: int = 409, code: str = "PLAN_CHANGE_REFUSED") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Preview:
    direction: str  # UPGRADE or DOWNGRADE
    effective: str  # NOW or AT_RENEWAL
    effective_at: datetime
    amount_now_micros: int
    currency: str
    next_renewal_micros: int
    plan_name: str
    seats: int
    blockers: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _live(db: Session, organization_id: uuid.UUID, *, lock: bool = False) -> Subscription:
    from app.services.billing import subscription_service

    subscription = subscription_service.live_subscription_for_organization(db, organization_id=organization_id)
    if subscription is None:
        raise PlanChangeError("There is no subscription to change. Choose a plan to subscribe.", code="NO_SUBSCRIPTION")
    if subscription.gateway != "DODO" or not subscription.gateway_subscription_id:
        raise PlanChangeError("This subscription's plan is changed in the billing portal.", code="NOT_DODO")
    if lock:
        db.execute(select(Subscription.id).where(Subscription.id == subscription.id).with_for_update())
    return subscription


def _tier(db: Session, key: str) -> QuotaTier:
    from app.services import quota_service

    tier = quota_service.published_tier_by_key(db, key=key)
    if tier is None:
        raise PlanChangeError(f"There is no plan called {key!r}.", status=422, code="UNKNOWN_PLAN")
    if not tier.gateway_price_id or tier.unit_amount_micros is None:
        raise PlanChangeError(f"{tier.display_name} is sold by quote: email us to move to it.", code="QUOTED_PLAN")
    return tier


def _entry(db: Session, tier: QuotaTier, limit_key: str) -> Optional[Decimal]:
    value = db.execute(
        select(QuotaTierEntry.max_quantity).where(
            QuotaTierEntry.quota_tier_id == tier.id, QuotaTierEntry.limit_key == limit_key
        )
    ).scalar_one_or_none()
    return Decimal(value) if value is not None else None


def downgrade_blockers(db: Session, *, organization_id: uuid.UUID, tier: QuotaTier, seats: int) -> list[dict[str, str]]:
    """What must change before the organization fits `tier` with `seats` (N-003)."""
    from app.models.workspace import Workspace, WorkspaceStatus
    from app.services import plan_admission
    from app.services.plan_admission import STORAGE
    from app.services.billing import seat_service

    blockers: list[dict[str, str]] = []
    in_use = seat_service.billable_seats(db, organization_id=organization_id)
    seat_cap = _entry(db, tier, SEATS_LIMIT)
    allowed = min(seats, int(seat_cap)) if seat_cap is not None else seats
    if in_use > allowed:
        blockers.append({
            "code": "SEATS_IN_USE",
            "message": (f"{in_use} people hold seats and {tier.display_name} with this choice allows {allowed}. "
                        f"Remove {in_use - allowed} {'person' if in_use - allowed == 1 else 'people'} first."),
        })
    workspace_cap = _entry(db, tier, WORKSPACES_LIMIT)
    if workspace_cap is not None:
        active = int(db.execute(
            select(func.count()).select_from(Workspace).where(
                Workspace.organization_id == organization_id, Workspace.status == WorkspaceStatus.ACTIVE,
                Workspace.is_sample.is_(False))
        ).scalar_one())
        if active > int(workspace_cap):
            blockers.append({
                "code": "WORKSPACES_OVER_LIMIT",
                "message": (f"{active} workspaces are active and {tier.display_name} includes {int(workspace_cap)}. "
                            f"Archive {active - int(workspace_cap)} first."),
            })
    storage_cap = _entry(db, tier, STORAGE)
    if storage_cap is not None:
        stored = plan_admission.stored_bytes(db, (organization_id,))
        if Decimal(stored) > storage_cap * _GIB:
            blockers.append({
                "code": "STORAGE_OVER_LIMIT",
                "message": (f"{(Decimal(stored) / _GIB).quantize(Decimal('0.1'))} GB is stored and "
                            f"{tier.display_name} includes {storage_cap.normalize()} GB. Delete documents first."),
            })
    return blockers


def preview(db: Session, *, organization_id: uuid.UUID, tier_key: str, seats: int) -> Preview:
    from app.services.billing.dodo_gateway import get_dodo_gateway

    subscription = _live(db, organization_id)
    target = _tier(db, tier_key)
    if seats < 1:
        raise PlanChangeError("At least one seat.", status=422, code="SEATS_INVALID")
    if target.key == subscription.quota_tier_key:
        raise PlanChangeError("That is the current plan. Seats are changed under Seats.", code="SAME_PLAN")
    current = db.get(QuotaTier, subscription.quota_tier_id)
    current_price = int(current.unit_amount_micros or 0) if current is not None else 0
    upgrade = int(target.unit_amount_micros) > current_price
    renewal = int(target.unit_amount_micros) * seats
    if upgrade:
        quote = get_dodo_gateway().preview_change_plan(
            subscription_id=subscription.gateway_subscription_id, product_id=target.gateway_price_id,
            quantity=seats, proration_mode=UPGRADE_MODE)
        return Preview(direction="UPGRADE", effective="NOW", effective_at=_now(),
                       amount_now_micros=quote.prorated_amount_micros, currency=quote.currency,
                       next_renewal_micros=renewal, plan_name=target.display_name, seats=seats)
    return Preview(direction="DOWNGRADE", effective="AT_RENEWAL", effective_at=subscription.current_period_end,
                   amount_now_micros=0, currency=(target.currency or "USD").upper(), next_renewal_micros=renewal,
                   plan_name=target.display_name, seats=seats,
                   blockers=downgrade_blockers(db, organization_id=organization_id, tier=target, seats=seats))


def apply(db: Session, *, organization_id: uuid.UUID, actor_id: uuid.UUID, tier_key: str, seats: int,
          expected_amount_micros: int) -> dict[str, Any]:
    """Make the previewed change: an upgrade now, a downgrade scheduled for the renewal."""
    from app.services import audit_service
    from app.services.billing import dodo_reconcile_service
    from app.services.billing.dodo_gateway import get_dodo_gateway

    subscription = _live(db, organization_id, lock=True)
    shown = preview(db, organization_id=organization_id, tier_key=tier_key, seats=seats)
    if int(expected_amount_micros) != shown.amount_now_micros:
        raise PlanChangeError(
            "The amount changed since you looked (the period moved on, or prices changed). Review it again.",
            code="AMOUNT_CHANGED")
    if shown.blockers:
        raise PlanChangeError(" ".join(b["message"] for b in shown.blockers), code="DOWNGRADE_BLOCKED")
    target = _tier(db, tier_key)
    if shown.direction == "UPGRADE":
        snapshot = get_dodo_gateway().change_plan(
            subscription_id=subscription.gateway_subscription_id, product_id=target.gateway_price_id,
            quantity=seats, proration_mode=UPGRADE_MODE, on_payment_failure="prevent_change")
        dodo_reconcile_service.apply_snapshot(db, snapshot=snapshot, organization_id=organization_id,
                                              source="plan_change.upgrade")
        subscription = db.get(Subscription, subscription.id)
        subscription.pending_quota_tier_key = subscription.pending_seats = None
        subscription.pending_effective_at = subscription.pending_requested_at = None
        subscription.pending_requested_by = None
    else:
        subscription.pending_quota_tier_key = target.key
        subscription.pending_seats = seats
        subscription.pending_effective_at = subscription.current_period_end
        subscription.pending_requested_at = _now()
        subscription.pending_requested_by = actor_id
    db.flush()
    audit_service.record(
        db, organization_id=organization_id, actor_id=actor_id, resource_type=AuditResourceType.SUBSCRIPTION,
        resource_id=subscription.id, action=AuditAction.UPDATED,
        details={"event": f"plan_change_{shown.direction.lower()}", "to_tier": target.key, "seats": seats,
                 "amount_now_micros": shown.amount_now_micros, "effective_at": shown.effective_at.isoformat()},
    )
    return {**shown.as_dict(), "subscription_id": subscription.id}


def withdraw(db: Session, *, organization_id: uuid.UUID, actor_id: uuid.UUID) -> bool:
    """Withdraw a scheduled downgrade; False when none was scheduled."""
    from app.services import audit_service

    subscription = _live(db, organization_id, lock=True)
    if subscription.pending_quota_tier_key is None:
        return False
    tier_key = subscription.pending_quota_tier_key
    subscription.pending_quota_tier_key = subscription.pending_seats = None
    subscription.pending_effective_at = subscription.pending_requested_at = None
    subscription.pending_requested_by = None
    db.flush()
    audit_service.record(
        db, organization_id=organization_id, actor_id=actor_id, resource_type=AuditResourceType.SUBSCRIPTION,
        resource_id=subscription.id, action=AuditAction.UPDATED,
        details={"event": "plan_change_withdrawn", "to_tier": tier_key},
    )
    return True


def apply_due(db: Session, *, now: Optional[datetime] = None, limit: int = 100) -> list[dict[str, Any]]:
    """Make at Dodo every scheduled downgrade whose renewal is within APPLY_AHEAD (the 15-minute sweep)."""
    from app.services import organization_notification_service as notices
    from app.services.billing import dodo_reconcile_service
    from app.services.billing.dodo_gateway import get_dodo_gateway

    moment = now or _now()
    due = db.execute(
        select(Subscription)
        .where(Subscription.gateway == "DODO", Subscription.pending_quota_tier_key.is_not(None),
               Subscription.pending_effective_at <= moment + APPLY_AHEAD)
        .order_by(Subscription.pending_effective_at)
        .limit(limit)
        .with_for_update(of=Subscription, skip_locked=True)
    ).scalars().all()
    applied: list[dict[str, Any]] = []
    for subscription in due:
        organization_id = db.execute(
            select(BillingAccount.organization_id).where(BillingAccount.id == subscription.billing_account_id)
        ).scalar_one()
        tier = _tier(db, subscription.pending_quota_tier_key)
        seats = int(subscription.pending_seats or subscription.seats_purchased)
        snapshot = get_dodo_gateway().change_plan(
            subscription_id=subscription.gateway_subscription_id, product_id=tier.gateway_price_id,
            quantity=seats, proration_mode=DOWNGRADE_MODE, on_payment_failure="apply_change")
        subscription.pending_quota_tier_key = subscription.pending_seats = None
        subscription.pending_effective_at = subscription.pending_requested_at = None
        subscription.pending_requested_by = None
        db.flush([subscription])
        dodo_reconcile_service.apply_snapshot(db, snapshot=snapshot, organization_id=organization_id,
                                              source="plan_change.downgrade")
        notices.emit_billing(
            db, organization_id=organization_id, title=f"Your plan is now {tier.display_name}",
            message=(f"The change you scheduled took effect for the new billing period: {tier.display_name}, "
                     f"{seats} seat{'s' if seats != 1 else ''}. The renewal is billed at the new price."),
            priority=_info(),
        )
        applied.append({"subscription_id": subscription.id, "tier": tier.key, "seats": seats})
        logger.info("billing.downgrade_applied", extra={"subscription_id": str(subscription.id), "tier": tier.key})
    return applied


def _info():
    from app.models.notification import NotificationPriority

    return NotificationPriority.INFO


__all__ = ["APPLY_AHEAD", "PlanChangeError", "Preview", "apply", "apply_due", "downgrade_blockers", "preview",
           "withdraw"]
