"""ARCH-30 Tranche 2 (D-6, D-8) — add-on ledger transitions, grace and halting.

D-6, AS APPROVED
================

Removing a custom domain or a warehouse sync the moment an entitlement ends
breaks DNS and TLS for the customer's own users, and stops a data pipeline
someone downstream depends on, with no warning. The approved behaviour is a
14-day grace with notifications, after which domain routing and sync jobs stop.

THE STATE MACHINE
=================

    ACTIVE --grant lost, resources exist--> GRACE --window ends--> LAPSED
    ACTIVE --grant lost, no resources-----> LAPSED
    GRACE  --grant restored---------------> ACTIVE
    LAPSED --grant restored---------------> ACTIVE   (halted resources are NOT
                                                      silently resumed)

Restoration does not re-enable schedules or re-verify hostnames. A domain that
was taken offline two months ago may point somewhere else now; serving it again
without a fresh DNS proof would be re-opening invariant 2 of ARCH-25 by way of
a billing event. The restored notification says exactly what to do instead.

WHAT "HALT" DOES, PRECISELY
===========================

    addon.custom_domain   every VERIFIED hostname goes through
                          `domain_service.revoke_domain`: status REVOKED,
                          certificate cleared, claim retained.
    addon.warehouse_sync  every enabled export schedule is disabled.
                          Destinations, credentials and run history remain.

Both are the platform's existing, audited operations. Nothing here deletes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.config import settings
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.custom_domain import DOMAIN_STATUS_REVOKED, DOMAIN_STATUS_VERIFIED, CustomDomain
from app.models.organization_addon import (
    ADDON_STATUS_ACTIVE,
    ADDON_STATUS_GRACE,
    ADDON_STATUS_LAPSED,
    PURCHASE_ACTIVE,
    PURCHASE_ENDED,
    PURCHASE_NONE,
    PURCHASE_ON_HOLD,
    STAGE_GRACE_ENDING,
    STAGE_GRACE_STARTED,
    STAGE_HALTED,
    STAGE_RESTORED,
    OrganizationAddon,
)
from app.models.warehouse_sync import ExportSchedule, WarehouseDestination
from app.services import audit_service, organization_notification_service
from app.services.billing import entitlement_service
from app.services.billing.entitlement_service import ADDON_CATALOG

logger = logging.getLogger("app.services.billing.addon")

#: How long before the end of grace the second notice goes out.
GRACE_ENDING_NOTICE = timedelta(days=3)

_RESOURCE_TYPES = {
    entitlements.CUSTOM_DOMAIN_ADDON: AuditResourceType.CUSTOM_DOMAIN,
    entitlements.WAREHOUSE_SYNC_ADDON: AuditResourceType.WAREHOUSE_DESTINATION,
}


class AddonError(Exception):
    """Base class for add-on refusals."""


class AddonAlreadyGrantedError(AddonError):
    """The organization already has this add-on; a checkout would double-bill."""


class AddonPurchaseConflictError(AddonError):
    """A second live gateway subscription arrived for an add-on already bought."""


def _audit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
    event: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {"addon_key": addon_key, "event": event}
    payload.update(details or {})
    audit_service.record(
        db,
        organization_id=organization_id,
        resource_type=_RESOURCE_TYPES[addon_key],
        action=AuditAction.UPDATED,
        details=payload,
    )


# ============================================================================
# Purchase state, written by the gateway reconciler only
# ============================================================================


def record_purchase_state(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
    gateway: str,
    gateway_subscription_id: str,
    gateway_status: str,
    state_version: int,
    now: Optional[datetime] = None,
) -> tuple[OrganizationAddon, bool]:
    """Apply a re-fetched gateway subscription to the ledger's purchase columns.

    Guarded by `gateway_state_version`, the same monotonic rule as
    `subscriptions.stripe_state_version`: a fetch issued earlier that lands
    later is a no-op, so out-of-order webhooks cannot resurrect a cancellation.
    """
    entitlement_service.offer_for(addon_key)
    moment = now or entitlement_service.utcnow()
    row = entitlement_service.ledger_row(
        db, organization_id=organization_id, addon_key=addon_key, for_update=True
    )
    if row is None:
        row = OrganizationAddon(
            organization_id=organization_id,
            addon_key=addon_key,
            status=ADDON_STATUS_ACTIVE,
            granted_by=None,
            purchase_status=PURCHASE_NONE,
            gateway_state_version=0,
        )
        db.add(row)
        db.flush([row])

    if (
        row.gateway_subscription_id
        and row.gateway_subscription_id != gateway_subscription_id
        and row.purchase_status in (PURCHASE_ACTIVE, PURCHASE_ON_HOLD)
    ):
        raise AddonPurchaseConflictError(
            f"Organization {organization_id} already holds live {addon_key} "
            f"subscription {row.gateway_subscription_id}; refusing to overwrite "
            f"it with {gateway_subscription_id}. One of the two is a duplicate "
            "charge — cancel it at the gateway."
        )

    if int(state_version) <= int(row.gateway_state_version or 0):
        return row, False

    raw = (gateway_status or "").strip().lower()
    if raw == "active":
        purchase, purchase_grace = PURCHASE_ACTIVE, None
    elif raw == "on_hold":
        purchase = PURCHASE_ON_HOLD
        purchase_grace = (
            row.purchase_grace_ends_at
            if row.purchase_status == PURCHASE_ON_HOLD and row.purchase_grace_ends_at
            else moment + entitlement_service.on_hold_grace_period()
        )
    elif raw in ("cancelled", "canceled", "expired", "failed", "paused"):
        purchase, purchase_grace = PURCHASE_ENDED, None
    elif raw == "pending":
        purchase, purchase_grace = row.purchase_status, row.purchase_grace_ends_at
    else:
        raise AddonError(
            f"Gateway reported add-on subscription status {gateway_status!r}, "
            "which this ledger does not model. Refusing rather than guessing."
        )

    row.purchase_status = purchase
    row.purchase_grace_ends_at = purchase_grace
    row.gateway = gateway
    row.gateway_subscription_id = gateway_subscription_id
    row.gateway_state_version = int(state_version)
    db.flush([row])
    return row, True


# ============================================================================
# The single place grants, grace and halting are decided
# ============================================================================


def reconcile_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    now: Optional[datetime] = None,
    only: Optional[str] = None,
) -> list[dict[str, Any]]:
    moment = now or entitlement_service.utcnow()
    keys = [only] if only else list(entitlements.ADDON_KEYS)
    return [
        _reconcile_one(db, organization_id=organization_id, addon_key=key, now=moment)
        for key in keys
    ]


def _result(addon_key: str, previous: Optional[str], row: Optional[OrganizationAddon], **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "addon_key": addon_key,
        "previous_status": previous,
        "status": row.status if row is not None else None,
        "changed": previous != (row.status if row is not None else None),
    }
    out.update(extra)
    return out


def _format_day(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d %b %Y")


def _reconcile_one(
    db: Session, *, organization_id: uuid.UUID, addon_key: str, now: datetime
) -> dict[str, Any]:
    offer = ADDON_CATALOG[addon_key]
    row = entitlement_service.ledger_row(
        db, organization_id=organization_id, addon_key=addon_key, for_update=True
    )
    previous = row.status if row is not None else None
    source = entitlement_service.grant_source(
        db, organization_id=organization_id, addon_key=addon_key, row=row, now=now
    )
    resources = entitlement_service.live_resource_count(
        db, organization_id=organization_id, addon_key=addon_key
    )

    # ---- granted ---------------------------------------------------------
    if source is not None:
        if row is None:
            row = OrganizationAddon(
                organization_id=organization_id,
                addon_key=addon_key,
                status=ADDON_STATUS_ACTIVE,
                granted_by=source,
            )
            db.add(row)
            db.flush([row])
            return _result(addon_key, None, row, source=source)

        if row.status == ADDON_STATUS_ACTIVE and row.granted_by == source:
            return _result(addon_key, previous, row, source=source)

        was_restricted = row.status in (ADDON_STATUS_GRACE, ADDON_STATUS_LAPSED)
        was_halted = row.halted_at is not None
        row.status = ADDON_STATUS_ACTIVE
        row.granted_by = source
        row.grace_started_at = None
        row.grace_ends_at = None
        row.lapsed_at = None
        row.halted_at = None
        if was_restricted:
            organization_notification_service.notify_addon_restored(
                db,
                organization_id=organization_id,
                addon_name=offer.display_name,
                resources_were_halted=was_halted,
            )
            row.last_notified_stage = STAGE_RESTORED
            _audit(
                db,
                organization_id=organization_id,
                addon_key=addon_key,
                event="addon_restored",
                details={"source": source, "resources_were_halted": was_halted},
            )
        db.flush([row])
        return _result(addon_key, previous, row, source=source)

    # ---- not granted -----------------------------------------------------
    if row is None:
        if resources == 0:
            return _result(addon_key, None, None)
        row = OrganizationAddon(
            organization_id=organization_id,
            addon_key=addon_key,
            status=ADDON_STATUS_ACTIVE,
            granted_by=None,
        )
        db.add(row)
        db.flush([row])

    if row.status == ADDON_STATUS_ACTIVE:
        if resources == 0:
            row.status = ADDON_STATUS_LAPSED
            row.lapsed_at = now
            row.grace_started_at = None
            row.grace_ends_at = None
            db.flush([row])
            _audit(
                db,
                organization_id=organization_id,
                addon_key=addon_key,
                event="addon_lapsed_without_resources",
            )
            return _result(addon_key, previous, row)

        row.status = ADDON_STATUS_GRACE
        row.grace_started_at = now
        row.grace_ends_at = now + entitlement_service.grace_period()
        row.last_notified_stage = STAGE_GRACE_STARTED
        db.flush([row])
        organization_notification_service.notify_addon_grace_started(
            db,
            organization_id=organization_id,
            addon_name=offer.display_name,
            halt_effect=offer.halt_effect,
            grace_ends_on=_format_day(row.grace_ends_at),
            resource_count=resources,
            resource_noun=offer.resource_noun,
        )
        _audit(
            db,
            organization_id=organization_id,
            addon_key=addon_key,
            event="addon_grace_started",
            details={
                "grace_ends_at": row.grace_ends_at.isoformat(),
                "live_resources": resources,
            },
        )
        return _result(addon_key, previous, row, grace_ends_at=row.grace_ends_at.isoformat())

    if row.status == ADDON_STATUS_GRACE and row.grace_ends_at is not None:
        if now >= row.grace_ends_at:
            halted = halt_resources(db, organization_id=organization_id, addon_key=addon_key)
            row.status = ADDON_STATUS_LAPSED
            row.lapsed_at = now
            row.halted_at = now
            row.last_notified_stage = STAGE_HALTED
            db.flush([row])
            organization_notification_service.notify_addon_halted(
                db,
                organization_id=organization_id,
                addon_name=offer.display_name,
                halt_effect=offer.halt_effect,
            )
            _audit(
                db,
                organization_id=organization_id,
                addon_key=addon_key,
                event="addon_halted",
                details=halted,
            )
            return _result(addon_key, previous, row, halted=halted)

        if (
            row.grace_ends_at - now <= GRACE_ENDING_NOTICE
            and row.last_notified_stage == STAGE_GRACE_STARTED
        ):
            organization_notification_service.notify_addon_grace_ending(
                db,
                organization_id=organization_id,
                addon_name=offer.display_name,
                halt_effect=offer.halt_effect,
                grace_ends_on=_format_day(row.grace_ends_at),
            )
            row.last_notified_stage = STAGE_GRACE_ENDING
            db.flush([row])
        return _result(addon_key, previous, row)

    return _result(addon_key, previous, row)


def halt_resources(
    db: Session, *, organization_id: uuid.UUID, addon_key: str
) -> dict[str, Any]:
    if addon_key == entitlements.CUSTOM_DOMAIN_ADDON:
        from app.services.branding import domain_service

        domains = list(
            db.execute(
                select(CustomDomain).where(
                    CustomDomain.organization_id == organization_id,
                    CustomDomain.status == DOMAIN_STATUS_VERIFIED,
                )
            ).scalars()
        )
        hostnames: list[str] = []
        for domain in domains:
            domain_service.revoke_domain(db, domain=domain, actor_id=None)
            hostnames.append(domain.hostname)
        logger.warning(
            "addon.custom_domains_halted",
            extra={"organization_id": str(organization_id), "hostnames": hostnames},
        )
        return {"domains_revoked": hostnames}

    if addon_key == entitlements.WAREHOUSE_SYNC_ADDON:
        schedules = list(
            db.execute(
                select(ExportSchedule).where(
                    ExportSchedule.organization_id == organization_id,
                    ExportSchedule.enabled.is_(True),
                )
            ).scalars()
        )
        for schedule in schedules:
            schedule.enabled = False
        db.flush()
        paused = [str(schedule.id) for schedule in schedules]
        logger.warning(
            "addon.warehouse_sync_halted",
            extra={"organization_id": str(organization_id), "schedules": paused},
        )
        return {"schedules_paused": paused}

    raise entitlement_service.UnknownAddonError(f"{addon_key!r} is not a known add-on.")


def sweep(
    db: Session, *, now: Optional[datetime] = None, limit: int = 500
) -> dict[str, Any]:
    """Reconcile every organization that could be affected by an add-on change.

    Candidates are organizations with a ledger row that is not settled LAPSED,
    or with live add-on resources. A tenant with neither has nothing to grant,
    protect or halt, and scanning it would make this job O(tenants).
    """
    moment = now or entitlement_service.utcnow()
    candidates: set[uuid.UUID] = set()

    candidates.update(
        db.execute(
            select(OrganizationAddon.organization_id).where(
                or_(
                    OrganizationAddon.status != ADDON_STATUS_LAPSED,
                    OrganizationAddon.purchase_status.in_(
                        (PURCHASE_ACTIVE, PURCHASE_ON_HOLD)
                    ),
                )
            )
        ).scalars()
    )
    candidates.update(
        db.execute(
            select(CustomDomain.organization_id)
            .where(CustomDomain.status != DOMAIN_STATUS_REVOKED)
            .distinct()
        ).scalars()
    )
    candidates.update(
        db.execute(select(WarehouseDestination.organization_id).distinct()).scalars()
    )

    ordered = sorted(candidates, key=str)[: max(1, int(limit))]
    transitions: list[dict[str, Any]] = []
    for organization_id in ordered:
        for result in reconcile_organization(db, organization_id=organization_id, now=moment):
            if result.get("changed") or result.get("halted"):
                transitions.append({"organization_id": str(organization_id), **result})

    return {
        "candidates": len(candidates),
        "reconciled": len(ordered),
        "transitions": transitions[:100],
        "grace_days": int(getattr(settings, "BILLING_ADDON_GRACE_DAYS", 14)),
    }


# ============================================================================
# Self-serve purchase
# ============================================================================


def create_addon_checkout(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
):
    """Start a gateway checkout for one add-on. Returns portal_service.EphemeralSession."""
    from app.services.billing import account_service, portal_service
    from app.services.billing.payment_gateway import (
        active_gateway_name,
        get_payment_gateway,
    )

    offer = entitlement_service.offer_for(addon_key)
    access = entitlement_service.addon_access(
        db, organization_id=organization_id, addon_key=addon_key
    )
    if access.state == entitlement_service.STATE_ACTIVE:
        raise AddonAlreadyGrantedError(
            f"{offer.display_name} is already included for this organization "
            f"(through {access.source.lower() if access.source else 'a grant'}). "
            "Starting a checkout would bill for something already provided."
        )

    if not entitlement_service.purchasable(addon_key):
        raise portal_service.CheckoutConfigurationError(
            f"{offer.display_name} is not available for self-serve purchase on "
            "this deployment. Contact sales, or upgrade to a plan that includes it."
        )

    gateway_name = active_gateway_name()
    product_id = str(getattr(settings, offer.product_setting))
    account = account_service.get_for_organization(db, organization_id=organization_id)
    customer_id = (
        account.gateway_customer_id
        if account is not None and account.gateway == gateway_name
        else None
    )
    customer_email = (
        None
        if customer_id
        else account_service.default_billing_email(db, organization_id=organization_id)
    )

    session = get_payment_gateway(gateway_name).create_checkout_session(
        customer_id=customer_id,
        customer_email=customer_email,
        price_id=product_id,
        quantity=1,
        success_url=str(settings.BILLING_CHECKOUT_SUCCESS_URL or ""),
        cancel_url=str(settings.BILLING_CHECKOUT_CANCEL_URL or ""),
        client_reference_id=str(organization_id),
        metadata={"organization_id": str(organization_id), "addon_key": addon_key},
    )

    expires_at = (
        datetime.fromtimestamp(int(session.expires_at_epoch), tz=timezone.utc)
        if session.expires_at_epoch
        else None
    )
    logger.info(
        "addon.checkout_started",
        extra={"organization_id": str(organization_id), "addon_key": addon_key},
    )
    return portal_service.EphemeralSession(
        url=session.url,
        expires_at=expires_at,
        kind="checkout",
        stripe_session_id=session.session_id,
    )


__all__ = [
    "AddonAlreadyGrantedError",
    "AddonError",
    "AddonPurchaseConflictError",
    "GRACE_ENDING_NOTICE",
    "create_addon_checkout",
    "halt_resources",
    "reconcile_organization",
    "record_purchase_state",
    "sweep",
]
