"""ARCH-30 Tranche 2 (D-8) — may this organization use an add-on, answered once.

THE DEFECT THIS CLOSES
======================

`custom_domains.py` and `warehouse_sync.py` checked a ROLE and nothing else.
A Free organization's owner could claim `ai.acme.com`, have it verified,
receive a certificate, and register a Snowflake destination with a nightly
push — the two add-ons priced at $199 and $300 a month, delivered for nothing,
because the only question either router asked was "is this person the owner".

THE TWO GRANT SOURCES
=====================

    TIER          the organization's resolved quota tier carries a
                  `quota_tier_entries` row keyed by the add-on. Enterprise v3
                  bundles both. Presence is the grant, exactly as
                  `llm.platform_key` works.
    SUBSCRIPTION  the add-on was bought as its own gateway subscription and
                  the ledger's `purchase_status` says it is paid for.

Both are read here and nowhere else. `addon_service` writes the ledger;
`app.api.addon_gate` refuses requests; the entitlements endpoint renders lock
cards. All three ask `addon_access`, so the console cannot show an unlocked
card over an endpoint that answers 402.

WHY THE RESOLVER NEVER WRITES
=============================

It runs on request paths guarded by role dependencies that may be read-only.
Grace windows are STAMPED by the sweep and by the gateway reconciler. When the
resolver sees a grant disappear before either has run, it answers GRACE with a
provisional end date rather than LAPSED: the failure mode of a 15-minute sweep
lag must be fourteen days and fifteen minutes of access, never an outage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.config import settings
from app.models.custom_domain import DOMAIN_STATUS_REVOKED, CustomDomain
from app.models.organization_addon import (
    ADDON_STATUS_ACTIVE,
    ADDON_STATUS_GRACE,
    ADDON_STATUS_LAPSED,
    GRANTED_BY_SUBSCRIPTION,
    GRANTED_BY_TIER,
    PURCHASE_ACTIVE,
    PURCHASE_ON_HOLD,
    OrganizationAddon,
)
from app.models.warehouse_sync import WarehouseDestination

STATE_ACTIVE = "ACTIVE"
STATE_GRACE = "GRACE"
STATE_LAPSED = "LAPSED"
STATE_NOT_GRANTED = "NOT_GRANTED"


@dataclass(frozen=True)
class AddonOffer:
    """What an add-on is, what it costs, and what losing it does."""

    key: str
    display_name: str
    description: str
    monthly_price_micros: int
    currency: str
    #: `settings` attribute holding the gateway product id. An attribute NAME,
    #: not a value: product ids differ between test and live mode.
    product_setting: str
    #: Plain words for what happens when grace ends. Rendered in notifications
    #: and in the console, so it must be true of `addon_service.halt_resources`.
    halt_effect: str
    resource_noun: str


ADDON_CATALOG: dict[str, AddonOffer] = {
    entitlements.CUSTOM_DOMAIN_ADDON: AddonOffer(
        key=entitlements.CUSTOM_DOMAIN_ADDON,
        display_name="Custom domains",
        description=(
            "Serve FlowPilot on your own hostname, with TLS certificates issued "
            "and renewed for you and branding shown before sign-in."
        ),
        monthly_price_micros=199_000_000,
        currency="USD",
        product_setting="DODO_ADDON_PRODUCT_CUSTOM_DOMAIN",
        halt_effect=(
            "verified hostnames are taken offline. You keep the claim, so no "
            "other organization can take the name"
        ),
        resource_noun="custom domain",
    ),
    entitlements.WAREHOUSE_SYNC_ADDON: AddonOffer(
        key=entitlements.WAREHOUSE_SYNC_ADDON,
        display_name="Warehouse sync",
        description=(
            "Scheduled exports of usage, documents, assistant and automation "
            "activity into Snowflake, BigQuery, S3 or your own warehouse."
        ),
        monthly_price_micros=300_000_000,
        currency="USD",
        product_setting="DODO_ADDON_PRODUCT_WAREHOUSE_SYNC",
        halt_effect=(
            "scheduled exports are paused. Destinations and run history are "
            "kept"
        ),
        resource_noun="warehouse destination",
    ),
}


def _assert_catalogued() -> None:
    """Refuse to import if the catalog and the entitlement vocabulary drift."""
    registered = set(entitlements.ADDON_KEYS)
    catalogued = set(ADDON_CATALOG)
    if registered != catalogued:
        raise RuntimeError(
            "Add-on catalog and entitlement registry disagree: "
            f"registered={sorted(registered)} catalogued={sorted(catalogued)}."
        )


_assert_catalogued()


@dataclass(frozen=True)
class AddonAccess:
    addon_key: str
    state: str
    source: Optional[str]
    grace_ends_at: Optional[datetime]
    live_resource_count: int

    @property
    def can_create(self) -> bool:
        """New resources: a new hostname, a new destination, a new schedule."""
        return self.state == STATE_ACTIVE

    @property
    def can_maintain(self) -> bool:
        """Keeping existing resources working during a downgrade grace."""
        return self.state in (STATE_ACTIVE, STATE_GRACE)


class UnknownAddonError(ValueError):
    """An add-on key that is not in the catalog."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def grace_period() -> timedelta:
    return timedelta(days=int(getattr(settings, "BILLING_ADDON_GRACE_DAYS", 14)))


def on_hold_grace_period() -> timedelta:
    return timedelta(days=int(getattr(settings, "BILLING_ON_HOLD_GRACE_DAYS", 13)))


def offer_for(addon_key: str) -> AddonOffer:
    try:
        return ADDON_CATALOG[addon_key]
    except KeyError as exc:
        raise UnknownAddonError(f"{addon_key!r} is not a known add-on.") from exc


def ledger_row(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
    for_update: bool = False,
) -> Optional[OrganizationAddon]:
    stmt = select(OrganizationAddon).where(
        OrganizationAddon.organization_id == organization_id,
        OrganizationAddon.addon_key == addon_key,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def tier_grants(db: Session, *, organization_id: uuid.UUID, addon_key: str) -> bool:
    from app.services import quota_service  # local: quota_service is heavy

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    if tier is None:
        return False
    return any(entry.limit_key == addon_key for entry in tier.entries)


def purchase_grants(row: Optional[OrganizationAddon], *, now: datetime) -> bool:
    if row is None:
        return False
    if row.purchase_status == PURCHASE_ACTIVE:
        return True
    return bool(
        row.purchase_status == PURCHASE_ON_HOLD
        and row.purchase_grace_ends_at is not None
        and row.purchase_grace_ends_at > now
    )


def grant_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
    row: Optional[OrganizationAddon],
    now: datetime,
) -> Optional[str]:
    """TIER before SUBSCRIPTION: a bundled grant survives a cancelled purchase."""
    if tier_grants(db, organization_id=organization_id, addon_key=addon_key):
        return GRANTED_BY_TIER
    if purchase_grants(row, now=now):
        return GRANTED_BY_SUBSCRIPTION
    return None


def live_resource_count(
    db: Session, *, organization_id: uuid.UUID, addon_key: str
) -> int:
    """Resources a lapse would affect. Zero means a lapse breaks nothing."""
    if addon_key == entitlements.CUSTOM_DOMAIN_ADDON:
        stmt = select(func.count()).select_from(CustomDomain).where(
            CustomDomain.organization_id == organization_id,
            CustomDomain.status != DOMAIN_STATUS_REVOKED,
        )
    elif addon_key == entitlements.WAREHOUSE_SYNC_ADDON:
        stmt = select(func.count()).select_from(WarehouseDestination).where(
            WarehouseDestination.organization_id == organization_id,
        )
    else:
        raise UnknownAddonError(f"{addon_key!r} is not a known add-on.")
    return int(db.execute(stmt).scalar_one() or 0)


def addon_access(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_key: str,
    now: Optional[datetime] = None,
) -> AddonAccess:
    offer_for(addon_key)
    moment = now or utcnow()
    row = ledger_row(db, organization_id=organization_id, addon_key=addon_key)
    source = grant_source(
        db, organization_id=organization_id, addon_key=addon_key, row=row, now=moment
    )
    resources = live_resource_count(
        db, organization_id=organization_id, addon_key=addon_key
    )

    if source is not None:
        return AddonAccess(addon_key, STATE_ACTIVE, source, None, resources)

    if row is not None:
        if (
            row.status == ADDON_STATUS_GRACE
            and row.grace_ends_at is not None
            and row.grace_ends_at > moment
        ):
            return AddonAccess(addon_key, STATE_GRACE, None, row.grace_ends_at, resources)
        if row.status == ADDON_STATUS_ACTIVE:
            # The grant vanished since the sweep last looked. Provisional grace,
            # never an outage caused by sweep latency.
            return AddonAccess(
                addon_key, STATE_GRACE, None, moment + grace_period(), resources
            )
        if row.status == ADDON_STATUS_LAPSED:
            return AddonAccess(addon_key, STATE_LAPSED, None, None, resources)
        # GRACE whose window has passed and has not been swept yet.
        return AddonAccess(addon_key, STATE_LAPSED, None, row.grace_ends_at, resources)

    if resources > 0:
        # Resources that predate gating. Same fourteen days as a downgrade.
        return AddonAccess(addon_key, STATE_GRACE, None, moment + grace_period(), resources)

    return AddonAccess(addon_key, STATE_NOT_GRANTED, None, None, 0)


def purchasable(addon_key: str) -> bool:
    """Self-serve purchase needs a Merchant-of-Record gateway and a product id."""
    from app.services.billing.payment_gateway import active_gateway_name

    offer = offer_for(addon_key)
    return active_gateway_name() == "DODO" and bool(
        getattr(settings, offer.product_setting, None)
    )


def tiers_including(db: Session, addon_key: str) -> list[str]:
    """Display names of published tiers in force that bundle this add-on."""
    from app.services import quota_service

    names: list[str] = []
    for tier in quota_service.list_published_tiers(db, at=utcnow()):
        if any(entry.limit_key == addon_key for entry in tier.entries):
            if tier.display_name not in names:
                names.append(tier.display_name)
    return names


__all__ = [
    "ADDON_CATALOG",
    "AddonAccess",
    "AddonOffer",
    "STATE_ACTIVE",
    "STATE_GRACE",
    "STATE_LAPSED",
    "STATE_NOT_GRANTED",
    "UnknownAddonError",
    "addon_access",
    "grace_period",
    "grant_source",
    "ledger_row",
    "live_resource_count",
    "offer_for",
    "on_hold_grace_period",
    "purchasable",
    "purchase_grants",
    "tier_grants",
    "tiers_including",
    "utcnow",
]
