"""Chargebacks: recorded, told, and a reversed payment held read-only (website and payments session, C5).

Dodo Payments, as merchant of record, answers a dispute with the card network. What Servabo owns:

* the record (`billing_disputes`), kept to Dodo's current status for the dispute;
* telling the operator (an alert per dispute, and another when the payment is reversed) and the
  organization's owners and billing members (billing notices are always emailed, N-062);
* a reversed payment (a lost or accepted dispute) makes the organization read-only, with the
  reason "payment reversed by your bank", until a later payment succeeds (`clear_on_payment`) or
  the operator lifts it from the platform admin (`lift`). Reading, exporting and deleting go on,
  as in every read-only state.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditResourceType
from app.models.billing_dispute import REVERSED_STATUSES, BillingDispute
from app.models.notification import NotificationPriority
from app.models.organization import Organization

logger = logging.getLogger("app.services.billing.dispute")

#: The read-only reason a member sees (`/billing/access-summary`).
REASON_PAYMENT_REVERSED = "PAYMENT_REVERSED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reversal_holding(db: Session, *, organization_id: uuid.UUID) -> Optional[BillingDispute]:
    """The reversed payment that holds the organization read-only, if any."""
    return db.execute(
        select(BillingDispute)
        .where(
            BillingDispute.organization_id == organization_id,
            BillingDispute.reversed_at.is_not(None),
            BillingDispute.cleared_at.is_(None),
        )
        .order_by(BillingDispute.reversed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _money(micros: Optional[int], currency: Optional[str]) -> str:
    from app.core.display_format import format_amount_micros

    return format_amount_micros(int(micros or 0), currency or "USD") if micros else "a payment"


def record(
    db: Session,
    *,
    organization_id: uuid.UUID,
    gateway: str,
    dispute_id: str,
    payment_id: str,
    status: str,
    amount_micros: Optional[int],
    currency: Optional[str],
    opened_at: Optional[datetime],
    invoice_id: Optional[uuid.UUID],
) -> tuple[BillingDispute, dict[str, bool]]:
    """Record the gateway's current status for one dispute; what changed."""
    from app.services import operator_alert_service
    from app.services import organization_notification_service as notices

    status = (status or "").strip().lower()
    row = db.execute(
        select(BillingDispute)
        .where(BillingDispute.gateway == gateway, BillingDispute.gateway_dispute_id == dispute_id)
        .with_for_update()
    ).scalar_one_or_none()
    changes = {"opened": False, "reversed": False, "changed": False}
    now = _now()
    if row is None:
        row = BillingDispute(
            organization_id=organization_id, gateway=gateway, gateway_dispute_id=dispute_id,
            gateway_payment_id=payment_id, invoice_id=invoice_id, status=status,
            amount_micros=amount_micros, currency=currency, opened_at=opened_at or now,
        )
        db.add(row)
        changes["opened"] = changes["changed"] = True
    elif row.status != status:
        row.status = status
        changes["changed"] = True
    if invoice_id is not None and row.invoice_id is None:
        row.invoice_id = invoice_id
    if status in REVERSED_STATUSES and row.reversed_at is None:
        row.reversed_at = now
        changes["reversed"] = True
    db.flush([row])

    organization = db.get(Organization, organization_id)
    who = f"{organization.name} ({organization.slug})" if organization is not None else str(organization_id)
    amount = _money(amount_micros, currency)
    if changes["opened"]:
        operator_alert_service.record(
            db, kind="PAYMENT_DISPUTED", dedupe_key=f"dispute:{gateway}:{dispute_id}",
            subject=f"Payment disputed: {who}",
            body=(f"The customer's bank disputed {amount} (payment {payment_id}, dispute {dispute_id}). "
                  "Dodo Payments, as merchant of record, answers the bank; add your evidence in the Dodo "
                  "dashboard (Disputes) before its deadline. RUNBOOK section 9.8."),
        )
        notices.emit_billing(
            db, organization_id=organization_id,
            title=f"A payment of {amount} was disputed",
            message=("Your bank or card issuer has disputed a payment to Servabo. If this was not "
                     "intended, contact your bank to withdraw it, or reply to this email. Nothing "
                     "changes in your account while the dispute is open."),
            priority=NotificationPriority.WARNING,
        )
    if changes["reversed"]:
        operator_alert_service.record(
            db, kind="PAYMENT_REVERSED", dedupe_key=f"dispute_reversed:{gateway}:{dispute_id}",
            subject=f"Payment reversed by the bank: {who} is read-only",
            body=(f"Dispute {dispute_id} on payment {payment_id} ({amount}) ended with the money returned to the "
                  "customer. The organization is read-only until a later payment succeeds or you lift it "
                  "(Platform → RevOps → Disputes)."),
        )
        notices.emit_billing(
            db, organization_id=organization_id,
            title="Payment reversed by your bank: the organization is read-only",
            message=(f"Your bank reversed a payment of {amount} to Servabo, so this organization is read-only: "
                     "you can still read, export and delete everything. It returns to normal when the next "
                     "payment succeeds. If the reversal was a mistake, reply to this email."),
            priority=NotificationPriority.WARNING,
        )
    if changes["changed"]:
        from app.services import audit_service

        audit_service.record(
            db, organization_id=organization_id, resource_type=AuditResourceType.BILLING_ACCOUNT,
            action=AuditAction.UPDATED,
            details={"event": "payment_dispute", "gateway": gateway, "dispute_id": dispute_id,
                     "payment_id": payment_id, "status": status, "reversed": row.reversed_at is not None},
        )
    return row, changes


def clear_on_payment(db: Session, *, organization_id: uuid.UUID, paid_at: datetime) -> int:
    """A payment that succeeded after a reversal lifts it; how many were lifted."""
    from app.services import organization_notification_service as notices

    held = db.execute(
        select(BillingDispute).where(
            BillingDispute.organization_id == organization_id,
            BillingDispute.reversed_at.is_not(None),
            BillingDispute.cleared_at.is_(None),
            BillingDispute.reversed_at < paid_at,
        ).with_for_update()
    ).scalars().all()
    for row in held:
        row.cleared_at = _now()
        row.cleared_reason = "a later payment succeeded"
    if held:
        db.flush(held)
        notices.emit_billing(
            db, organization_id=organization_id, title="Payment received: the organization is writable again",
            message="A new payment succeeded after the reversed one, so the organization is back to normal.",
            priority=NotificationPriority.INFO,
        )
    return len(held)


def lift(db: Session, *, dispute: BillingDispute, actor_user_id: uuid.UUID, reason: str) -> BillingDispute:
    """The operator lifts a reversal's hold (platform admin)."""
    from app.services import audit_service

    if dispute.cleared_at is None:
        dispute.cleared_at = _now()
        dispute.cleared_by_user_id = actor_user_id
        dispute.cleared_reason = reason.strip()[:500] or "lifted by the operator"
        db.flush([dispute])
        audit_service.record(
            db, organization_id=dispute.organization_id, actor_id=actor_user_id,
            resource_type=AuditResourceType.BILLING_ACCOUNT, action=AuditAction.UPDATED,
            details={"event": "payment_reversal_lifted", "dispute_id": dispute.gateway_dispute_id,
                     "reason": dispute.cleared_reason},
        )
    return dispute


def view(dispute: BillingDispute, organization: Optional[Organization] = None) -> dict[str, Any]:
    return {
        "id": str(dispute.id),
        "organization_id": str(dispute.organization_id),
        "organization_name": organization.name if organization is not None else None,
        "gateway": dispute.gateway,
        "gateway_dispute_id": dispute.gateway_dispute_id,
        "gateway_payment_id": dispute.gateway_payment_id,
        "status": dispute.status,
        "amount_micros": dispute.amount_micros,
        "currency": dispute.currency,
        "opened_at": dispute.opened_at,
        "reversed_at": dispute.reversed_at,
        "cleared_at": dispute.cleared_at,
        "cleared_reason": dispute.cleared_reason,
        "holding_read_only": dispute.reversed_at is not None and dispute.cleared_at is None,
    }


__all__ = ["REASON_PAYMENT_REVERSED", "clear_on_payment", "lift", "record", "reversal_holding", "view"]
