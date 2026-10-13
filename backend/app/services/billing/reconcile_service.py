"""ARCH-15 Step 15.2 — reconcile state; do not apply deltas (F2)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.models.stripe_inbound_event import StripeInboundEvent
from app.services.billing import (
    account_service,
    seat_service,
    stripe_gateway,
    subscription_service,
)
from app.services.billing.stripe_gateway import (
    StripeEvent,
    StripeObjectNotFoundError,
)

logger = logging.getLogger("app.services.billing.reconcile")


class ReconcileRefused(Exception):
    """The event cannot be reconciled and retrying will not help."""


@dataclass(frozen=True)
class ReconcileOutcome:
    handled: bool
    detail: dict[str, Any]
    ignored_reason: Optional[str] = None
    organization_id: Optional[uuid.UUID] = None

    @classmethod
    def processed(
        cls, organization_id: Optional[uuid.UUID] = None, **detail: Any
    ) -> "ReconcileOutcome":
        return cls(handled=True, detail=detail, organization_id=organization_id)

    @classmethod
    def ignored(cls, reason: str, **detail: Any) -> "ReconcileOutcome":
        return cls(handled=False, detail=detail, ignored_reason=reason)


Reconciler = Callable[[Session, StripeEvent], ReconcileOutcome]


def _subscription_id(event: StripeEvent) -> str:
    obj = event.data_object
    if str(obj.get("object") or "") == "subscription" and obj.get("id"):
        return str(obj["id"])
    subscription = obj.get("subscription")
    if isinstance(subscription, dict):
        return str(subscription.get("id") or "")
    if subscription:
        return str(subscription)
    raise ReconcileRefused(
        f"Event {event.id} ({event.type}) names no subscription to re-fetch."
    )


def _customer_id(event: StripeEvent) -> str:
    obj = event.data_object
    if str(obj.get("object") or "") == "customer" and obj.get("id"):
        return str(obj["id"])
    customer = obj.get("customer")
    if isinstance(customer, dict):
        return str(customer.get("id") or "")
    if customer:
        return str(customer)
    raise ReconcileRefused(
        f"Event {event.id} ({event.type}) names no customer to re-fetch."
    )


def reconcile_subscription(db: Session, event: StripeEvent) -> ReconcileOutcome:
    subscription_id = _subscription_id(event)
    gateway = stripe_gateway.get_gateway()

    try:
        snapshot = gateway.fetch_subscription(subscription_id)
    except StripeObjectNotFoundError:
        logger.error(
            "billing.subscription_missing_at_stripe",
            extra={
                "stripe_subscription_id": subscription_id,
                "stripe_event_id": event.id,
            },
        )
        return ReconcileOutcome.ignored(
            "subscription_missing_at_stripe",
            stripe_subscription_id=subscription_id,
        )

    account = account_service.get_by_customer_id(
        db, stripe_customer_id=snapshot.customer_id
    )
    if account is None:
        account = _adopt_customer_from_metadata(db, snapshot.customer_id)

    # Fallback to subscription metadata if customer carried no organization_id
    if account is None and snapshot.metadata and snapshot.metadata.get("organization_id"):
        try:
            org_id = uuid.UUID(str(snapshot.metadata["organization_id"]))
            account = account_service.adopt_customer(
                db,
                organization_id=org_id,
                stripe_customer_id=snapshot.customer_id,
                currency=snapshot.currency,
            )
        except (ValueError, TypeError):
            pass

    if account is None:
        raise ReconcileRefused(
            f"Stripe customer {snapshot.customer_id} maps to no organization. "
            "Set `metadata.organization_id` on the customer, or create the "
            "billing account before the subscription. Refusing to attach a "
            "subscription to a tenant we cannot name."
        )

    if snapshot.currency:
        account_service.assert_currency(db, currency=snapshot.currency)

    duplicate = _duplicate_of_live_subscription(db, account=account, snapshot=snapshot)
    if duplicate is not None:
        return duplicate

    subscription, applied = subscription_service.upsert_from_stripe(
        db, account=account, snapshot=snapshot
    )

    drift = seat_service.detect_drift(
        db, organization_id=account.organization_id
    )
    if drift is not None and drift.has_drift:
        logger.error("billing.seat_drift", extra=drift.as_dict())
        seat_service.request_seat_sync(
            db,
            organization_id=account.organization_id,
            reason="observed_during_reconcile",
            drift=drift,
        )

    return ReconcileOutcome.processed(
        organization_id=account.organization_id,
        stripe_subscription_id=snapshot.id,
        status=snapshot.status,
        seats_purchased=snapshot.seats,
        state_version=snapshot.state_version,
        applied=applied,
        superseded=not applied,
        seat_drift=drift.as_dict() if drift is not None else None,
    )


def reconcile_customer(db: Session, event: StripeEvent) -> ReconcileOutcome:
    customer_id = _customer_id(event)
    gateway = stripe_gateway.get_gateway()

    try:
        snapshot = gateway.fetch_customer(customer_id)
    except StripeObjectNotFoundError:
        return ReconcileOutcome.ignored(
            "customer_missing_at_stripe", stripe_customer_id=customer_id
        )

    if snapshot.deleted:
        logger.warning(
            "billing.customer_deleted_at_stripe",
            extra={"stripe_customer_id": customer_id},
        )
        return ReconcileOutcome.ignored(
            "customer_deleted_at_stripe", stripe_customer_id=customer_id
        )

    account = account_service.get_by_customer_id(db, stripe_customer_id=customer_id)
    if account is None:
        account = _adopt_customer_from_metadata(db, customer_id, snapshot=snapshot)

    if account is None:
        return ReconcileOutcome.ignored(
            "customer_unmapped_to_org", stripe_customer_id=customer_id
        )

    return ReconcileOutcome.processed(
        organization_id=account.organization_id,
        stripe_customer_id=customer_id,
        billing_email=account.billing_email,
    )


def _adopt_customer_from_metadata(
    db: Session,
    customer_id: str,
    *,
    snapshot: Optional[stripe_gateway.StripeCustomerSnapshot] = None,
) -> Optional[Any]:
    resolved = snapshot
    if resolved is None:
        try:
            resolved = stripe_gateway.get_gateway().fetch_customer(customer_id)
        except stripe_gateway.StripeGatewayError:
            return None

    raw_org = (resolved.metadata or {}).get("organization_id")
    if not raw_org:
        return None

    try:
        organization_id = uuid.UUID(str(raw_org))
    except (TypeError, ValueError):
        logger.error(
            "billing.customer_metadata_org_unparseable",
            extra={"stripe_customer_id": customer_id, "value": str(raw_org)},
        )
        return None

    return account_service.adopt_customer(
        db,
        organization_id=organization_id,
        stripe_customer_id=customer_id,
        billing_email=resolved.email,
        currency=resolved.currency,
    )


def reconcile_invoice(db: Session, event: StripeEvent) -> ReconcileOutcome:
    """Re-fetch a Stripe invoice and align payment status."""
    from app.services.billing import invoice_service

    obj = event.data_object
    stripe_invoice_id = str(obj.get("id") or "")
    if not stripe_invoice_id:
        raise ReconcileRefused(f"Event {event.id} names no invoice.")

    snapshot = stripe_gateway.get_gateway().fetch_invoice(stripe_invoice_id)

    account = account_service.get_by_customer_id(
        db, stripe_customer_id=snapshot.customer_id
    )
    if account is None:
        account = _adopt_customer_from_metadata(db, snapshot.customer_id)

    # Fallback to subscription metadata if customer had no metadata
    if account is None and snapshot.subscription_id:
        try:
            sub_snapshot = stripe_gateway.get_gateway().fetch_subscription(snapshot.subscription_id)
            if sub_snapshot.metadata and sub_snapshot.metadata.get("organization_id"):
                org_id = uuid.UUID(str(sub_snapshot.metadata["organization_id"]))
                account = account_service.adopt_customer(
                    db,
                    organization_id=org_id,
                    stripe_customer_id=snapshot.customer_id,
                    currency=snapshot.currency,
                )
        except Exception:
            pass

    if account is None:
        return ReconcileOutcome.ignored(
            "invoice_unmapped_to_org", stripe_invoice_id=stripe_invoice_id
        )

    invoice = invoice_service.get_by_stripe_id(
        db, stripe_invoice_id=stripe_invoice_id
    )

    if invoice is None:
        return ReconcileOutcome.processed(
            organization_id=account.organization_id,
            stripe_invoice_id=stripe_invoice_id,
            action="assembly_pending",
            stripe_total_cents=snapshot.total_cents,
        )

    paid_micros = snapshot.amount_paid_cents * invoice_service.MICROS_PER_CENT
    invoice_service.record_payment(
        db, invoice=invoice, amount_paid_micros=paid_micros
    )

    comparison = invoice_service.compare_with_stripe(
        db, invoice=invoice, stripe_total_cents=snapshot.total_cents
    )

    if event.type in ("invoice.payment_failed",):
        from app.services.billing import dunning_service

        dunning_result = dunning_service.on_payment_failed(
            db, invoice=invoice, stripe_event_id=event.id
        )
    elif event.type in ("invoice.paid", "invoice.payment_succeeded"):
        from app.services.billing import dunning_service

        dunning_result = dunning_service.on_payment_succeeded(db, invoice=invoice)
    else:
        dunning_result = None

    return ReconcileOutcome.processed(
        organization_id=account.organization_id,
        stripe_invoice_id=stripe_invoice_id,
        invoice_number=invoice.number,
        amount_paid_micros=paid_micros,
        totals_agree=comparison.within_tolerance,
        delta_micros=comparison.delta_micros,
        dunning=dunning_result,
    )


def reconcile_charge_refund(db: Session, event: StripeEvent) -> ReconcileOutcome:
    """Record the refunds of a charge against the invoice it paid (campaign session 4, N-069).

    `charge.refunded` carries the charge; `charge.refund.updated` (a refund that later failed or
    was cancelled) carries the refund, which names its charge. Either way the charge is re-fetched
    and its running refunded total is written to the invoice, so delivery order does not matter.
    """
    from app.services.billing import invoice_service

    obj = event.data_object
    object_id = str(obj.get("id") or "")
    if not object_id:
        raise ReconcileRefused(f"Event {event.id} names no charge or refund.")
    gateway = stripe_gateway.get_gateway()
    charge_id = gateway.fetch_refund_charge_id(object_id) if object_id.startswith("re_") else object_id

    charge = gateway.fetch_charge(charge_id)
    if not charge.invoice_id:
        return ReconcileOutcome.ignored("refund_without_invoice", charge_id=charge_id)
    invoice = invoice_service.get_by_stripe_id(db, stripe_invoice_id=charge.invoice_id)
    if invoice is None:
        return ReconcileOutcome.ignored(
            "refund_unmapped_to_invoice", charge_id=charge_id, stripe_invoice_id=charge.invoice_id
        )

    refunded_micros = charge.amount_refunded_cents * invoice_service.MICROS_PER_CENT
    change = invoice_service.record_refund(
        db, invoice=invoice, amount_refunded_micros=refunded_micros, refunded_at=event.created
    )
    organization_id = invoice.billing_account.organization_id
    if change > 0:
        from app.services import organization_notification_service as notices

        notices.notify_refund_recorded(
            db,
            organization_id=organization_id,
            invoice_number=invoice.number,
            amount_micros=change,
            currency=invoice.currency,
            fully_refunded=invoice.amount_refunded_micros >= invoice.amount_paid_micros,
        )
    return ReconcileOutcome.processed(
        organization_id=organization_id,
        stripe_invoice_id=charge.invoice_id,
        invoice_number=invoice.number,
        amount_refunded_micros=int(invoice.amount_refunded_micros),
        change_micros=change,
    )


def _duplicate_of_live_subscription(
    db: Session, *, account: Any, snapshot: Any
) -> Optional[ReconcileOutcome]:
    """A second live subscription for an organization that already has one (campaign session 4).

    An organization holds one live subscription (`uq_subscriptions_live_account`). Checkout is
    refused while one is live (F-253), but a checkout completed in a tab opened before the first
    one still creates a second subscription at the gateway, which charges the customer. Writing
    it violated the unique index on every attempt until the event dead-lettered, and nobody was
    told. Now it is not written, the event is settled as ignored with that reason, and the
    deployment's owner is alerted at once with the refund playbook (RUNBOOK §9.8).
    """
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES

    if subscription_service.get_by_stripe_id(db, stripe_subscription_id=snapshot.id) is not None:
        return None
    if subscription_service._coerce_status(snapshot.status) not in LIVE_SUBSCRIPTION_STATUSES:
        return None
    live = subscription_service.live_subscription_for_organization(db, organization_id=account.organization_id)
    if live is None or live.stripe_subscription_id == snapshot.id:
        return None

    from app.services import operator_alert_service

    operator_alert_service.duplicate_subscription(
        db,
        organization_id=account.organization_id,
        gateway="STRIPE",
        customer_id=snapshot.customer_id,
        recorded_subscription_id=str(live.stripe_subscription_id or live.gateway_subscription_id),
        duplicate_subscription_id=snapshot.id,
        duplicate_status=str(snapshot.status),
    )
    logger.warning(
        "billing.duplicate_live_subscription",
        extra={
            "organization_id": str(account.organization_id),
            "recorded_subscription_id": str(live.stripe_subscription_id),
            "duplicate_subscription_id": snapshot.id,
        },
    )
    return ReconcileOutcome.ignored(
        "duplicate_live_subscription",
        organization_id=str(account.organization_id),
        stripe_subscription_id=snapshot.id,
        recorded_subscription_id=str(live.stripe_subscription_id),
    )


# ============================================================================
# Dispatch table
# ============================================================================

RECONCILERS: dict[str, Reconciler] = {
    "customer.subscription.created": reconcile_subscription,
    "customer.subscription.updated": reconcile_subscription,
    "customer.subscription.deleted": reconcile_subscription,
    "customer.subscription.paused": reconcile_subscription,
    "customer.subscription.resumed": reconcile_subscription,
    "customer.subscription.pending_update_applied": reconcile_subscription,
    "customer.subscription.pending_update_expired": reconcile_subscription,
    "customer.subscription.trial_will_end": reconcile_subscription,
    "customer.created": reconcile_customer,
    "customer.updated": reconcile_customer,
    "customer.deleted": reconcile_customer,
    "invoice.finalized": reconcile_invoice,
    "invoice.paid": reconcile_invoice,
    "invoice.payment_succeeded": reconcile_invoice,
    "invoice.payment_failed": reconcile_invoice,
    "invoice.marked_uncollectible": reconcile_invoice,
    "invoice.voided": reconcile_invoice,
    "charge.refunded": reconcile_charge_refund,
    "charge.refund.updated": reconcile_charge_refund,
}

KNOWN_UNHANDLED: dict[str, str] = {
    "invoice.created": "assembled_on_period_close",
    "invoice.upcoming": "informational_only",
    "invoice.updated": "superseded_by_finalized",
    "invoiceitem.created": "we_do_not_push_line_items",
    "charge.succeeded": "covered_by_invoice_paid",
    "charge.failed": "covered_by_invoice_payment_failed",
    "payment_intent.succeeded": "covered_by_invoice_paid",
    "payment_intent.payment_failed": "covered_by_invoice_payment_failed",
    "payment_method.attached": "portal_owned",
    "payment_method.detached": "portal_owned",
    "checkout.session.completed": "superseded_by_subscription_created",
    "checkout.session.expired": "no_state_to_reconcile",
}


def reconcile_event(db: Session, event: StripeEvent) -> ReconcileOutcome:
    handler = RECONCILERS.get(event.type)
    if handler is not None:
        return handler(db, event)

    tranche = KNOWN_UNHANDLED.get(event.type)
    if tranche is not None:
        return ReconcileOutcome.ignored(
            "not_yet_implemented", event_type=event.type, owner=tranche
        )

    return ReconcileOutcome.ignored("unsubscribed_event_type", event_type=event.type)


def event_from_row(row: StripeInboundEvent) -> StripeEvent:
    return StripeEvent(
        id=row.stripe_event_id,
        type=row.event_type,
        created=row.stripe_created_at,
        livemode=bool(row.livemode),
        api_version=row.api_version,
        payload=dict(row.payload or {}),
    )


__all__ = [
    "KNOWN_UNHANDLED",
    "RECONCILERS",
    "ReconcileOutcome",
    "ReconcileRefused",
    "Reconciler",
    "reconcile_charge_refund",
    "event_from_row",
    "reconcile_customer",
    "reconcile_event",
    "reconcile_invoice",
    "reconcile_subscription",
]
