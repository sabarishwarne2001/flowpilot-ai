"""ARCH-15 Step 15.8 — dunning, and what "degraded" is allowed to mean."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.dunning_action import (
    DUNNING_STEP_ORDER,
    RESTRICTIVE_STEPS,
    DunningAction,
    DunningOutcome,
    DunningStep,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.models.notification import NotificationPriority, NotificationType
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)
from app.models.subscription import Subscription
from app.services.billing import account_service

logger = logging.getLogger("app.services.billing.dunning")


class BillingAccessState(str, PyEnum):
    """What a tenant may do, given its dunning position."""

    ACTIVE = "ACTIVE"
    RESTRICTED = "RESTRICTED"  # reads and export fine; writes refused
    SUSPENDED = "SUSPENDED"  # as above, plus no new work accepted

    @property
    def writes_allowed(self) -> bool:
        return self is BillingAccessState.ACTIVE

    @property
    def reads_allowed(self) -> bool:
        return True

    @property
    def export_allowed(self) -> bool:
        return True


@dataclass(frozen=True)
class DunningPosition:
    organization_id: uuid.UUID
    invoice_id: Optional[uuid.UUID]
    steps_applied: tuple[DunningStep, ...]
    next_step: Optional[DunningStep]
    access_state: BillingAccessState

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "invoice_id": str(self.invoice_id) if self.invoice_id else None,
            "steps_applied": [step.value for step in self.steps_applied],
            "next_step": self.next_step.value if self.next_step else None,
            "access_state": self.access_state.value,
        }


def _max_step() -> DunningStep:
    configured = (settings.BILLING_DUNNING_MAX_STEP or "").strip().upper()
    try:
        return DunningStep(configured)
    except ValueError:
        return DunningStep.NOTIFY_3


def _step_index(step: DunningStep) -> int:
    return DUNNING_STEP_ORDER.index(step)


def _organization_of(db: Session, invoice: Invoice) -> uuid.UUID:
    return db.execute(
        select(BillingAccount.organization_id).where(
            BillingAccount.id == invoice.billing_account_id
        )
    ).scalar_one()


def steps_applied(
    db: Session, *, subscription_id: uuid.UUID, invoice_id: uuid.UUID
) -> tuple[DunningStep, ...]:
    rows = (
        db.execute(
            select(DunningAction.step)
            .where(
                DunningAction.subscription_id == subscription_id,
                DunningAction.invoice_id == invoice_id,
                DunningAction.outcome == DunningOutcome.APPLIED,
            )
        )
        .scalars()
        .all()
    )
    return tuple(sorted(rows, key=_step_index))


def next_step(
    db: Session, *, subscription_id: uuid.UUID, invoice_id: uuid.UUID
) -> Optional[DunningStep]:
    applied = set(steps_applied(db, subscription_id=subscription_id, invoice_id=invoice_id))
    ceiling = _step_index(_max_step())
    for step in DUNNING_STEP_ORDER:
        if _step_index(step) > ceiling:
            return None
        if step not in applied:
            return step
    return None


def apply_step(
    db: Session,
    *,
    invoice: Invoice,
    subscription: Subscription,
    organization_id: uuid.UUID,
    step: DunningStep,
    stripe_event_id: Optional[str] = None,
) -> tuple[bool, Optional[DunningAction]]:
    if _step_index(step) > _step_index(_max_step()):
        logger.info(
            "dunning.step_above_ceiling",
            extra={
                "step": step.value,
                "ceiling": _max_step().value,
                "organization_id": str(organization_id),
            },
        )
        return False, None

    table = DunningAction.__table__
    stmt = (
        pg_insert(table)
        .values(
            organization_id=organization_id,
            subscription_id=subscription.id,
            invoice_id=invoice.id,
            step=step.value,
            outcome=DunningOutcome.APPLIED.value,
            stripe_event_id=stripe_event_id,
        )
        .on_conflict_do_nothing(
            constraint="uq_dunning_actions_subscription_invoice_step"
        )
        .returning(table.c.id)
    )
    action_id = db.execute(stmt).scalar_one_or_none()

    if action_id is None:
        logger.info(
            "dunning.step_already_applied",
            extra={
                "step": step.value,
                "invoice_number": invoice.number,
                "organization_id": str(organization_id),
            },
        )
        return False, None

    notified = 0
    if step in (DunningStep.NOTIFY_1, DunningStep.NOTIFY_2, DunningStep.NOTIFY_3):
        notified = _notify(
            db,
            organization_id=organization_id,
            invoice=invoice,
            step=step,
        )
    elif step in RESTRICTIVE_STEPS:
        account_service.mark_delinquent(db, organization_id=organization_id)
        notified = _notify(
            db, organization_id=organization_id, invoice=invoice, step=step
        )

    action = db.get(DunningAction, action_id)
    if action is not None:
        action.notified_user_count = notified
        action.detail = {
            "invoice_number": invoice.number,
            "amount_due_micros": invoice.amount_due_micros,
            "currency": invoice.currency,
        }
    db.flush()

    logger.warning(
        "dunning.step_applied",
        extra={
            "step": step.value,
            "invoice_number": invoice.number,
            "organization_id": str(organization_id),
            "notified_user_count": notified,
        },
    )
    return True, action


def _notify(
    db: Session,
    *,
    organization_id: uuid.UUID,
    invoice: Invoice,
    step: DunningStep,
) -> int:
    from app.services.organization_notification_service import _emit

    recipients = (
        db.execute(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MembershipStatus.ACTIVE,
                OrganizationMember.role.in_(
                    [
                        OrganizationRole.OWNER,
                        OrganizationRole.ADMIN,
                        OrganizationRole.BILLING,
                    ]
                ),
            )
        )
        .scalars()
        .all()
    )

    amount = invoice.amount_due_micros / 1_000_000
    messages = {
        DunningStep.NOTIFY_1: (
            "Payment could not be processed",
            f"We could not process payment for invoice {invoice.number} "
            f"({amount:.2f} {invoice.currency}). Please check the payment "
            "method on file — no action has been taken on your account.",
        ),
        DunningStep.NOTIFY_2: (
            "Payment still outstanding",
            f"Invoice {invoice.number} ({amount:.2f} {invoice.currency}) is "
            "still unpaid. Update your payment method to avoid interruption.",
        ),
        DunningStep.NOTIFY_3: (
            "Final notice before access changes",
            f"Invoice {invoice.number} ({amount:.2f} {invoice.currency}) "
            "remains unpaid. Access to create new work will be paused if it "
            "is not settled. Your data stays intact and export remains "
            "available at all times.",
        ),
        DunningStep.RESTRICT_WRITES: (
            "Account switched to read-only",
            f"Because invoice {invoice.number} is unpaid, your organization is "
            "temporarily read-only. Nothing has been deleted, everything "
            "remains readable, and export is still available. Settling the "
            "invoice restores full access.",
        ),
        DunningStep.SUSPEND_WRITES: (
            "Account suspended",
            f"Your organization is suspended pending payment of invoice "
            f"{invoice.number}. Your data is retained in full and export "
            "remains available.",
        ),
    }
    title, message = messages[step]

    for user_id in recipients:
        _emit(
            db,
            organization_id=organization_id,
            user_id=user_id,
            title=title,
            message=message,
            notification_type=NotificationType.SYSTEM,
            priority=(
                NotificationPriority.WARNING
                if step in RESTRICTIVE_STEPS
                else NotificationPriority.INFO
            ),
        )

    return len(recipients)


def on_payment_failed(
    db: Session,
    *,
    invoice: Invoice,
    stripe_event_id: Optional[str] = None,
) -> dict[str, Any]:
    if invoice.subscription_id is None:
        return {"outcome": "NO_SUBSCRIPTION", "invoice_number": invoice.number}

    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        return {"outcome": "NOT_COLLECTIBLE", "status": invoice.status.value}

    subscription = db.execute(
        select(Subscription).where(Subscription.id == invoice.subscription_id)
    ).scalar_one()
    organization_id = _organization_of(db, invoice)

    step = next_step(
        db, subscription_id=subscription.id, invoice_id=invoice.id
    )
    if step is None:
        return {
            "outcome": "SEQUENCE_EXHAUSTED",
            "invoice_number": invoice.number,
            "ceiling": _max_step().value,
        }

    applied, _ = apply_step(
        db,
        invoice=invoice,
        subscription=subscription,
        organization_id=organization_id,
        step=step,
        stripe_event_id=stripe_event_id,
    )

    return {
        "outcome": "APPLIED" if applied else "ALREADY_APPLIED",
        "step": step.value,
        "invoice_number": invoice.number,
        "organization_id": str(organization_id),
    }


def on_payment_succeeded(
    db: Session, *, invoice: Invoice
) -> dict[str, Any]:
    organization_id = _organization_of(db, invoice)
    account_service.clear_delinquency(db, organization_id=organization_id)
    logger.info(
        "dunning.cleared",
        extra={
            "organization_id": str(organization_id),
            "invoice_number": invoice.number,
        },
    )
    return {
        "outcome": "CLEARED",
        "organization_id": str(organization_id),
        "invoice_number": invoice.number,
    }


def access_state(db: Session, *, organization_id: uuid.UUID) -> BillingAccessState:
    row = db.execute(
        select(DunningAction.step)
        .join(Invoice, Invoice.id == DunningAction.invoice_id)
        .where(
            DunningAction.organization_id == organization_id,
            DunningAction.outcome == DunningOutcome.APPLIED,
            DunningAction.step.in_(RESTRICTIVE_STEPS),
            Invoice.status == InvoiceStatus.OPEN,
        )
        .order_by(DunningAction.applied_at.desc())
    ).scalars().all()

    if not row:
        return BillingAccessState.ACTIVE
    if DunningStep.SUSPEND_WRITES in row:
        return BillingAccessState.SUSPENDED
    return BillingAccessState.RESTRICTED


def position(db: Session, *, organization_id: uuid.UUID) -> DunningPosition:
    invoice = db.execute(
        select(Invoice)
        .join(BillingAccount, BillingAccount.id == Invoice.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Invoice.status == InvoiceStatus.OPEN,
        )
        .order_by(Invoice.period_start.asc())
        .limit(1)
    ).scalar_one_or_none()

    if invoice is None or invoice.subscription_id is None:
        return DunningPosition(
            organization_id=organization_id,
            invoice_id=None,
            steps_applied=(),
            next_step=None,
            access_state=access_state(db, organization_id=organization_id),
        )

    return DunningPosition(
        organization_id=organization_id,
        invoice_id=invoice.id,
        steps_applied=steps_applied(
            db, subscription_id=invoice.subscription_id, invoice_id=invoice.id
        ),
        next_step=next_step(
            db, subscription_id=invoice.subscription_id, invoice_id=invoice.id
        ),
        access_state=access_state(db, organization_id=organization_id),
    )


def overdue_invoices(
    db: Session, *, older_than: Optional[timedelta] = None, limit: int = 500
) -> list[Invoice]:
    cutoff = datetime.now(timezone.utc) - (older_than or timedelta(days=0))
    return list(
        db.execute(
            select(Invoice)
            .where(Invoice.status == InvoiceStatus.OPEN, Invoice.period_end <= cutoff)
            .order_by(Invoice.period_end.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


__all__ = [
    "BillingAccessState",
    "DunningPosition",
    "access_state",
    "apply_step",
    "next_step",
    "on_payment_failed",
    "on_payment_succeeded",
    "overdue_invoices",
    "position",
    "steps_applied",
]