"""ARCH-30 Tranche 2 (T4-F2e, D-9, D-10, D-11) — Dodo inbound reconciliation.

THE DEFECT THIS CLOSES
======================

A persisted Dodo event reached `handlers.billing._reconcile_claimed_row`, which
rebuilt it as a `StripeEvent` — `id=row.stripe_event_id`, NULL for Dodo — and
looked its type up in the Stripe-only `RECONCILERS`. `subscription.active` is
not a Stripe type, so every Dodo event was marked IGNORED as
`unsubscribed_event_type`. Correctly signed, correctly stored, and applied to
nothing. The worker now routes by `row.gateway`; this module is the DODO arm.

D-9: RECONCILE, DO NOT APPLY
============================

The event body is used for exactly one thing — finding the subscription id —
and then discarded. State comes from `GET /subscriptions/{id}`, fetched when
the event is processed. Dodo does not promise delivery order; an `on_hold`
arriving after the `active` that superseded it would otherwise suspend a
paying customer. The fetch returns current truth, and `stripe_state_version`
(epoch micros at fetch ISSUE, the column's established meaning) guards the
write so two concurrent fetches cannot land in the wrong order either.

D-11: GRACE IS OURS, AND IT IS NAMED
====================================

Dodo has no `past_due`. A failed renewal moves a subscription straight to
`on_hold`, and Payment Retries run inside a recovery window (13 days by
default). The approved behaviour — full access through a grace window, then a
read-only dunning state — is therefore implemented here, not read from Dodo:

    first on_hold observed   status past_due, grace_ends_at = now + 13 days
    later on_hold, in grace  status past_due, grace_ends_at unchanged
    on_hold after grace      status unpaid   (live, not entitled: read-only)
    active                   status active,  grace_ends_at cleared

`dunning_service.access_state` also derives RESTRICTED from a past_due row
whose grace has passed, so read-only begins on time even if no further
webhook arrives to move the row to unpaid.

D-10: A MERCHANT OF RECORD CREATES THE CUSTOMER
===============================================

Under Stripe, `ensure_billing_account` creates the customer before checkout.
Under Dodo, checkout creates it and we learn its id here. The billing account
is adopted on the first subscription event from `metadata.organization_id`,
which the checkout sets and Dodo carries onto the subscription.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.config import settings
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.billing_account import BillingAccount
from app.models.organization import Organization
from app.models.organization_addon import OrganizationAddon
from app.models.quota_tier import QuotaTier
from app.models.stripe_inbound_event import StripeInboundEvent
from app.models.subscription import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    Subscription,
    SubscriptionStatus,
)
from app.services import audit_service, organization_notification_service, quota_service
from app.services.billing import (
    account_service,
    addon_service,
    entitlement_service,
    subscription_service,
)
from app.services.billing.dodo_gateway import (
    GATEWAY_NAME,
    DodoObjectNotFoundError,
    DodoSubscriptionSnapshot,
    get_dodo_gateway,
)
from app.services.billing.reconcile_service import ReconcileOutcome, ReconcileRefused

logger = logging.getLogger("app.services.billing.dodo_reconcile")

Handler = Callable[[Session, StripeInboundEvent], ReconcileOutcome]

_TERMINAL = (SubscriptionStatus.CANCELED, SubscriptionStatus.INCOMPLETE_EXPIRED)


# ============================================================================
# Payload helpers — the body is only ever used to find what to re-fetch
# ============================================================================


def _data(row: StripeInboundEvent) -> dict[str, Any]:
    payload = row.payload or {}
    data = payload.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _subscription_id_from(row: StripeInboundEvent) -> Optional[str]:
    value = _data(row).get("subscription_id")
    return str(value) if value else None


def _uuid_or_none(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


# ============================================================================
# Resolution
# ============================================================================


def _organization_for(db: Session, snapshot: DodoSubscriptionSnapshot) -> uuid.UUID:
    claimed = _uuid_or_none((snapshot.metadata or {}).get("organization_id"))
    if claimed is not None:
        if db.get(Organization, claimed) is None:
            raise ReconcileRefused(
                f"Dodo subscription {snapshot.id} names organization {claimed}, "
                "which does not exist. Refusing to bill a tenant we cannot find."
            )
        return claimed

    by_customer = db.execute(
        select(BillingAccount.organization_id).where(
            BillingAccount.gateway == GATEWAY_NAME,
            BillingAccount.gateway_customer_id == snapshot.customer_id,
        )
    ).scalar_one_or_none()
    if by_customer is not None:
        return by_customer

    by_addon = db.execute(
        select(OrganizationAddon.organization_id).where(
            OrganizationAddon.gateway == GATEWAY_NAME,
            OrganizationAddon.gateway_subscription_id == snapshot.id,
        )
    ).scalar_one_or_none()
    if by_addon is not None:
        return by_addon

    raise ReconcileRefused(
        f"Dodo subscription {snapshot.id} (customer {snapshot.customer_id}) maps "
        "to no organization: no metadata.organization_id and no known customer. "
        "Set the metadata in the Dodo dashboard and replay the event."
    )


def _addon_key_for(snapshot: DodoSubscriptionSnapshot) -> Optional[str]:
    declared = (snapshot.metadata or {}).get("addon_key")
    if declared:
        if declared not in entitlements.ADDON_KEYS:
            raise ReconcileRefused(
                f"Dodo subscription {snapshot.id} declares unknown add-on "
                f"{declared!r}."
            )
        return str(declared)
    if snapshot.product_id:
        for key, offer in entitlement_service.ADDON_CATALOG.items():
            if getattr(settings, offer.product_setting, None) == snapshot.product_id:
                return key
    return None


def _tier_key_for(db: Session, snapshot: DodoSubscriptionSnapshot) -> str:
    declared = (snapshot.metadata or {}).get(subscription_service.TIER_METADATA_KEY)
    if declared:
        return str(declared).strip()
    if snapshot.product_id:
        # `uq_quota_tiers_gateway_price_id` makes this at most one row.
        key = db.execute(
            select(QuotaTier.key).where(QuotaTier.gateway_price_id == snapshot.product_id)
        ).scalar_one_or_none()
        if key:
            return str(key)
    raise ReconcileRefused(
        f"Dodo subscription {snapshot.id} carries no "
        f"metadata.{subscription_service.TIER_METADATA_KEY} and its product "
        f"{snapshot.product_id!r} is not the gateway price of any published tier."
    )


def _adopt_account(
    db: Session, *, organization_id: uuid.UUID, snapshot: DodoSubscriptionSnapshot
) -> BillingAccount:
    """Bind the Dodo customer to the organization, under the organization lock."""
    organization = db.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if organization is None:
        raise ReconcileRefused(f"Organization {organization_id} does not exist.")

    account = account_service.get_for_organization(db, organization_id=organization_id)
    if account is not None:
        if account.gateway == GATEWAY_NAME:
            if account.gateway_customer_id and account.gateway_customer_id != snapshot.customer_id:
                raise ReconcileRefused(
                    f"Organization {organization_id} is bound to Dodo customer "
                    f"{account.gateway_customer_id}; refusing to re-point it at "
                    f"{snapshot.customer_id}. One of the two is billing a card "
                    "nobody reconciles."
                )
            if not account.gateway_customer_id:
                account.gateway_customer_id = snapshot.customer_id
                db.flush([account])
            return account

        # A Stripe-bound account that never carried a subscription was created
        # by the pre-D-10 checkout path and billed nothing. It may migrate.
        has_history = db.execute(
            select(Subscription.id).where(Subscription.billing_account_id == account.id).limit(1)
        ).scalar_one_or_none()
        if has_history is not None:
            raise ReconcileRefused(
                f"Organization {organization_id} has {account.gateway} subscription "
                "history; refusing to attach a Dodo subscription alongside it. "
                "Cancel at the previous gateway and migrate deliberately."
            )
        account.gateway = GATEWAY_NAME
        account.gateway_customer_id = snapshot.customer_id
        db.flush([account])
        logger.warning(
            "billing.account_migrated_to_dodo",
            extra={"organization_id": str(organization_id)},
        )
        return account

    email = (snapshot.customer_email or "").strip().lower()
    if "@" not in email[1:] or len(email) > 320:
        email = account_service.default_billing_email(db, organization_id=organization_id)
    currency = (
        account_service.currency_in_force(db) or settings.BILLING_DEFAULT_CURRENCY
    ).upper()

    account = BillingAccount(
        organization_id=organization_id,
        gateway=GATEWAY_NAME,
        gateway_customer_id=snapshot.customer_id,
        stripe_customer_id=None,
        currency=currency,
        billing_email=email,
    )
    db.add(account)
    db.flush([account])
    logger.info(
        "billing_account.adopted_from_dodo",
        extra={"organization_id": str(organization_id), "currency": currency},
    )
    return account


def _existing(db: Session, gateway_subscription_id: str) -> Optional[Subscription]:
    return db.execute(
        select(Subscription).where(
            Subscription.gateway == GATEWAY_NAME,
            Subscription.gateway_subscription_id == gateway_subscription_id,
        )
    ).scalar_one_or_none()


def map_status(
    snapshot: DodoSubscriptionSnapshot,
    existing: Optional[Subscription],
    now: datetime,
) -> tuple[SubscriptionStatus, Optional[datetime], Optional[datetime]]:
    """Dodo status -> (internal status, grace_ends_at, canceled_at)."""
    raw = (snapshot.status or "").strip().lower()
    if raw == "active":
        return SubscriptionStatus.ACTIVE, None, None
    if raw == "on_hold":
        carried = (
            existing.grace_ends_at
            if existing is not None
            and existing.grace_ends_at is not None
            and existing.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID)
            else None
        )
        grace = carried or now + entitlement_service.on_hold_grace_period()
        status = SubscriptionStatus.PAST_DUE if now < grace else SubscriptionStatus.UNPAID
        return status, grace, None
    if raw in ("cancelled", "canceled", "expired"):
        return SubscriptionStatus.CANCELED, None, snapshot.cancelled_at or now
    if raw == "failed":
        return SubscriptionStatus.INCOMPLETE_EXPIRED, None, None
    if raw == "pending":
        return SubscriptionStatus.INCOMPLETE, None, None
    if raw == "paused":
        return SubscriptionStatus.PAUSED, None, None
    raise ReconcileRefused(
        f"Dodo reported subscription status {snapshot.status!r}. Not in the "
        "vocabulary this reconciler models; refusing rather than coercing."
    )


# ============================================================================
# Handlers
# ============================================================================


def reconcile_subscription_event(db: Session, row: StripeInboundEvent) -> ReconcileOutcome:
    subscription_id = _subscription_id_from(row)
    if not subscription_id:
        if row.event_type.startswith("payment."):
            return ReconcileOutcome.ignored(
                "payment_without_subscription", event_type=row.event_type
            )
        raise ReconcileRefused(
            f"Dodo event {row.gateway_event_id} ({row.event_type}) names no subscription."
        )

    try:
        snapshot = get_dodo_gateway().fetch_subscription(subscription_id)
    except DodoObjectNotFoundError:
        return ReconcileOutcome.ignored(
            "subscription_missing_at_dodo", gateway_subscription_id=subscription_id
        )

    organization_id = _organization_for(db, snapshot)
    addon_key = _addon_key_for(snapshot)
    if addon_key is not None:
        return _reconcile_addon(
            db, row=row, snapshot=snapshot, organization_id=organization_id, addon_key=addon_key
        )
    return _reconcile_plan(db, row=row, snapshot=snapshot, organization_id=organization_id)


def _reconcile_plan(
    db: Session,
    *,
    row: StripeInboundEvent,
    snapshot: DodoSubscriptionSnapshot,
    organization_id: uuid.UUID,
) -> ReconcileOutcome:
    now = entitlement_service.utcnow()
    account = _adopt_account(db, organization_id=organization_id, snapshot=snapshot)
    existing = _existing(db, snapshot.id)
    previous_status = existing.status if existing is not None else None
    previous_tier_key = existing.quota_tier_key if existing is not None else None

    tier, book = subscription_service.resolve_pins_for_key(
        db,
        requested_key=_tier_key_for(db, snapshot),
        period_start=snapshot.current_period_start,
        existing=existing,
        subscription_ref=snapshot.id,
    )
    status, grace_ends_at, canceled_at = map_status(snapshot, existing, now)

    cancel_at_period_end = bool(snapshot.cancel_at_next_billing_date)
    values: dict[str, Any] = {
        "billing_account_id": account.id,
        "gateway": GATEWAY_NAME,
        "gateway_subscription_id": snapshot.id,
        "stripe_subscription_id": None,
        "status": status.value,
        "quota_tier_key": tier.key,
        "quota_tier_id": tier.id,
        "price_book_id": book.id,
        "seats_purchased": max(1, int(snapshot.quantity)),
        "current_period_start": snapshot.current_period_start,
        "current_period_end": snapshot.current_period_end,
        "cancel_at_period_end": cancel_at_period_end,
        "cancel_at": snapshot.current_period_end if cancel_at_period_end else None,
        "canceled_at": canceled_at,
        "trial_end": None,
        "grace_ends_at": grace_ends_at,
        "stripe_state_version": int(snapshot.state_version),
        "last_reconciled_at": now,
    }

    table = Subscription.__table__
    stmt = pg_insert(table).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.gateway, table.c.gateway_subscription_id],
        index_where=text("gateway_subscription_id IS NOT NULL"),
        set_={
            key: getattr(stmt.excluded, key)
            for key in values
            if key not in ("gateway", "gateway_subscription_id")
        },
        where=table.c.stripe_state_version < stmt.excluded.stripe_state_version,
    ).returning(table.c.id)

    written_id = db.execute(stmt).scalar_one_or_none()
    if written_id is None:
        logger.info(
            "dodo.subscription_stale_write_ignored",
            extra={"gateway_subscription_id": snapshot.id},
        )
        return ReconcileOutcome.processed(
            organization_id=organization_id,
            gateway_subscription_id=snapshot.id,
            applied=False,
            superseded=True,
        )

    db.expire_all()
    subscription = db.get(Subscription, written_id)
    account = db.get(BillingAccount, account.id)

    fallback_error: Optional[str] = None
    if status in ENTITLED_SUBSCRIPTION_STATUSES:
        subscription_service.propagate_tier_to_organization(
            db, account=account, subscription=subscription
        )
    elif status in _TERMINAL:
        # Without this the organization keeps the paid tier: `resolve_tier`
        # falls back to `organizations.quota_tier_id` once no LIVE subscription
        # exists, and that pointer still names what was bought.
        try:
            quota_service.assign_tier(
                db,
                organization_id=organization_id,
                tier_key=str(getattr(settings, "BILLING_LAPSED_TIER_KEY", "free")),
            )
        except Exception as exc:  # noqa: BLE001
            fallback_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "dodo.fallback_tier_unassignable",
                extra={"organization_id": str(organization_id), "error": fallback_error},
            )

    changed = previous_status != status
    plan_changed = previous_tier_key is not None and previous_tier_key != tier.key
    if changed:
        organization_notification_service.notify_subscription_state_changed(
            db,
            organization_id=organization_id,
            plan_name=tier.display_name,
            previous_status=previous_status.value if previous_status else None,
            status=status.value,
            grace_ends_on=(grace_ends_at.strftime("%d %b %Y") if grace_ends_at else None),
        )
    if plan_changed:
        organization_notification_service.notify_plan_changed(
            db,
            organization_id=organization_id,
            previous_plan_key=previous_tier_key,
            plan_name=tier.display_name,
        )
    if changed or plan_changed:
        audit_service.record(
            db,
            organization_id=organization_id,
            resource_type=AuditResourceType.SUBSCRIPTION,
            resource_id=written_id,
            action=AuditAction.CREATED if existing is None else AuditAction.UPDATED,
            details={
                "gateway": GATEWAY_NAME,
                "gateway_event_id": row.gateway_event_id,
                "event_type": row.event_type,
                "gateway_subscription_id": snapshot.id,
                "from_status": previous_status.value if previous_status else None,
                "to_status": status.value,
                "from_tier": previous_tier_key,
                "to_tier": tier.key,
                "grace_ends_at": grace_ends_at.isoformat() if grace_ends_at else None,
                "window_normalised": snapshot.window_normalised,
            },
        )

    addon_results = addon_service.reconcile_organization(db, organization_id=organization_id, now=now)

    return ReconcileOutcome.processed(
        organization_id=organization_id,
        gateway_subscription_id=snapshot.id,
        status=status.value,
        previous_status=previous_status.value if previous_status else None,
        quota_tier_key=tier.key,
        seats_purchased=max(1, int(snapshot.quantity)),
        grace_ends_at=grace_ends_at.isoformat() if grace_ends_at else None,
        state_version=snapshot.state_version,
        applied=True,
        fallback_tier_error=fallback_error,
        addon_transitions=[r for r in addon_results if r.get("changed")],
    )


def _reconcile_addon(
    db: Session,
    *,
    row: StripeInboundEvent,
    snapshot: DodoSubscriptionSnapshot,
    organization_id: uuid.UUID,
    addon_key: str,
) -> ReconcileOutcome:
    now = entitlement_service.utcnow()
    try:
        ledger, applied = addon_service.record_purchase_state(
            db,
            organization_id=organization_id,
            addon_key=addon_key,
            gateway=GATEWAY_NAME,
            gateway_subscription_id=snapshot.id,
            gateway_status=snapshot.status,
            state_version=snapshot.state_version,
            now=now,
        )
    except addon_service.AddonError as exc:
        raise ReconcileRefused(str(exc)) from exc

    results = addon_service.reconcile_organization(
        db, organization_id=organization_id, now=now, only=addon_key
    )
    if applied:
        audit_service.record(
            db,
            organization_id=organization_id,
            resource_type=AuditResourceType.BILLING_ACCOUNT,
            action=AuditAction.UPDATED,
            details={
                "event": "addon_purchase_reconciled",
                "gateway": GATEWAY_NAME,
                "gateway_event_id": row.gateway_event_id,
                "event_type": row.event_type,
                "addon_key": addon_key,
                "gateway_subscription_id": snapshot.id,
                "purchase_status": ledger.purchase_status,
                "ledger_status": ledger.status,
            },
        )
    return ReconcileOutcome.processed(
        organization_id=organization_id,
        addon_key=addon_key,
        gateway_subscription_id=snapshot.id,
        purchase_status=ledger.purchase_status,
        ledger=results,
        applied=applied,
        superseded=not applied,
    )


RECONCILERS: dict[str, Handler] = {
    "subscription.active": reconcile_subscription_event,
    "subscription.renewed": reconcile_subscription_event,
    "subscription.on_hold": reconcile_subscription_event,
    "subscription.cancelled": reconcile_subscription_event,
    "subscription.failed": reconcile_subscription_event,
    "subscription.expired": reconcile_subscription_event,
    "subscription.plan_changed": reconcile_subscription_event,
    "subscription.updated": reconcile_subscription_event,
    # A payment names its subscription; re-fetching that subscription is the
    # whole reconciliation. A one-off payment names none and is ignored.
    "payment.succeeded": reconcile_subscription_event,
    "payment.failed": reconcile_subscription_event,
}

#: Deliberately not acted on, with the reason. Under a Merchant of Record the
#: vendor owns refunds and disputes as the legal seller.
KNOWN_UNHANDLED: dict[str, str] = {
    "payment.processing": "transient_state",
    "payment.cancelled": "no_state_to_reconcile",
    "refund.succeeded": "merchant_of_record_owned",
    "refund.failed": "merchant_of_record_owned",
    "dispute.opened": "merchant_of_record_owned",
    "dispute.expired": "merchant_of_record_owned",
    "dispute.accepted": "merchant_of_record_owned",
    "dispute.cancelled": "merchant_of_record_owned",
    "dispute.challenged": "merchant_of_record_owned",
    "dispute.won": "merchant_of_record_owned",
    "dispute.lost": "merchant_of_record_owned",
    "license_key.created": "not_sold",
}


def reconcile_row(db: Session, row: StripeInboundEvent) -> ReconcileOutcome:
    if (row.gateway or "").upper() != GATEWAY_NAME:
        raise ReconcileRefused(
            f"Inbound event {row.id} is gateway {row.gateway!r}; the Dodo "
            "reconciler refuses it rather than applying another vendor's payload."
        )
    handler = RECONCILERS.get(row.event_type)
    if handler is not None:
        return handler(db, row)
    reason = KNOWN_UNHANDLED.get(row.event_type)
    if reason is not None:
        return ReconcileOutcome.ignored(reason, event_type=row.event_type)
    return ReconcileOutcome.ignored("unsubscribed_event_type", event_type=row.event_type)


__all__ = [
    "KNOWN_UNHANDLED",
    "RECONCILERS",
    "map_status",
    "reconcile_row",
    "reconcile_subscription_event",
]