"""ARCH-15 Step 15.2 — reconcile state; do not apply deltas (F2).

READ THIS BEFORE CHANGING A HANDLER
===================================

The reasonable-sounding objection to everything below is: *"just apply the
event payload, the object is right there."* It is wrong, and it is wrong for a
reason that will not show up in code review, will not show up in staging, and
will corrupt billing state within a month of real traffic.

Stripe does not guarantee delivery order. `customer.subscription.updated` can
and does arrive before the `created` it followed. The instinct is to buffer and
sort by `event.created`; that fails twice over. `created` has second
granularity, so two events in the same second stay ambiguous no matter how long
you buffer — and a buffer needs a flush deadline, which is a guess about the
worst-case delivery delay of a system whose delivery delay you do not control.

So: **the event body is a cache-invalidation signal, not a payload.** Every
handler here reads exactly one thing out of `data.object` — an identifier — and
then re-fetches the object from the Stripe API. A stale event triggers a fetch
that returns current truth. Out-of-order delivery stops being a correctness
problem and becomes a redundant API call, which is a trade worth making every
time.

One race survives re-fetching, and only one: two events processed
concurrently, where the *older* fetch lands last. `stripe_state_version`
closes it. See `stripe_gateway.fetch_subscription` for why the version is
stamped when the fetch is issued rather than when it returns.

IGNORING IS A DECISION
======================

`KNOWN_UNHANDLED` names event types we have deliberately not implemented yet
and says which tranche owns them. `IGNORED` with a reason is an answer;
`PROCESSED` for something we never looked at is a lie that costs somebody a
day in six months.
"""

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
    """What the reconciler did, and whether the row is terminal."""

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


# ============================================================================
# Identifier extraction — the ONLY thing read from the event body
# ============================================================================


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


# ============================================================================
# Reconcilers
# ============================================================================


def reconcile_subscription(db: Session, event: StripeEvent) -> ReconcileOutcome:
    """Re-fetch a subscription and write it as authoritative."""
    subscription_id = _subscription_id(event)
    gateway = stripe_gateway.get_gateway()

    try:
        snapshot = gateway.fetch_subscription(subscription_id)
    except StripeObjectNotFoundError:
        # Stripe has forgotten the object. Deleting our row would destroy the
        # history 15.6 reproduces invoices against, so the row stays exactly
        # as it is and an operator is told. This is rare enough that a human
        # deciding is cheaper than a policy nobody remembers writing.
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

    if account is None:
        raise ReconcileRefused(
            f"Stripe customer {snapshot.customer_id} maps to no organization. "
            "Set `metadata.organization_id` on the customer, or create the "
            "billing account before the subscription. Refusing to attach a "
            "subscription to a tenant we cannot name."
        )

    if snapshot.currency:
        account_service.assert_currency(db, currency=snapshot.currency)

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
    """Re-fetch a customer and bind or refresh its billing account."""
    customer_id = _customer_id(event)
    gateway = stripe_gateway.get_gateway()

    try:
        snapshot = gateway.fetch_customer(customer_id)
    except StripeObjectNotFoundError:
        return ReconcileOutcome.ignored(
            "customer_missing_at_stripe", stripe_customer_id=customer_id
        )

    if snapshot.deleted:
        # A deleted Stripe customer does not delete our row. `ON DELETE
        # RESTRICT` exists so a tenant with billing history stays inspectable;
        # honouring a remote delete locally would defeat it.
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
        raise ReconcileRefused(
            f"Stripe customer {customer_id} carries no "
            "`metadata.organization_id` and matches no billing account. It "
            "belongs to no tenant we can name."
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
    """Bind a Stripe-side customer to its organization, if it names one.

    Checkout creates the customer at Stripe before we ever see an event about
    it, so this is the ordinary path for a first subscription, not an edge
    case. `metadata.organization_id` is written by our own Checkout session
    creation (15.7); a customer created by hand in the dashboard without it
    cannot be adopted, and saying so is better than guessing.
    """
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
}

#: Types we know about and have deliberately not implemented. Named, with the
#: tranche that owns them, so `IGNORED` is a decision an operator can audit
#: rather than a shrug.
KNOWN_UNHANDLED: dict[str, str] = {
    "invoice.created": "tranche_3_invoices",
    "invoice.finalized": "tranche_3_invoices",
    "invoice.paid": "tranche_3_invoices",
    "invoice.payment_succeeded": "tranche_3_invoices",
    "invoice.payment_failed": "tranche_4_dunning",
    "invoice.upcoming": "tranche_3_invoices",
    "invoice.updated": "tranche_3_invoices",
    "invoiceitem.created": "tranche_3_invoices",
    "charge.succeeded": "not_modelled_payment_intent_level",
    "charge.failed": "not_modelled_payment_intent_level",
    "charge.refunded": "tranche_3_invoices",
    "payment_intent.succeeded": "not_modelled_payment_intent_level",
    "payment_intent.payment_failed": "tranche_4_dunning",
    "payment_method.attached": "tranche_4_portal",
    "payment_method.detached": "tranche_4_portal",
    "checkout.session.completed": "tranche_4_checkout",
    "checkout.session.expired": "tranche_4_checkout",
}


def reconcile_event(db: Session, event: StripeEvent) -> ReconcileOutcome:
    """Route one verified event to its reconciler."""
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
    """Rebuild the verified event from the persisted row.

    Note what is *not* done here: the payload is not re-verified. Verification
    happened at the door, over the original bytes, and the bytes are gone —
    re-verifying a re-serialised body would fail for reasons that have nothing
    to do with authenticity. The row's existence is the assertion that it
    verified once.
    """
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
    "event_from_row",
    "reconcile_customer",
    "reconcile_event",
    "reconcile_subscription",
]