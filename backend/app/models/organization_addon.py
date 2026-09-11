"""ARCH-30 Tranche 2 (D-6, D-8) — the add-on ledger.

One row per (organization, add-on). Two facts, two columns:

    purchase_status   what the payment gateway says about a PURCHASED add-on
    status            what this platform currently GRANTS, after tier bundling
                      and the D-6 downgrade grace have been applied

`status` is written only by `addon_service.reconcile_organization`, which is
the single place that combines the two sources. `purchase_status` is written
only by the gateway reconciler. Neither writer infers the other's column, so
"the customer cancelled a redundant purchase while Enterprise still includes
the add-on" stays ACTIVE instead of starting a grace period nobody needed.

The row is not created for tenants that never touch an add-on. It appears when
a grant is first observed, when a purchase event arrives, or when the sweep
finds live add-on resources with no grant behind them — the last case being
tenants who configured a custom domain before gating existed, and who get the
same 14 days as anyone else rather than an outage on deploy day.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

ADDON_STATUS_ACTIVE = "ACTIVE"
ADDON_STATUS_GRACE = "GRACE"
ADDON_STATUS_LAPSED = "LAPSED"
ADDON_STATUS_VALUES: tuple[str, ...] = (
    ADDON_STATUS_ACTIVE,
    ADDON_STATUS_GRACE,
    ADDON_STATUS_LAPSED,
)

GRANTED_BY_TIER = "TIER"
GRANTED_BY_SUBSCRIPTION = "SUBSCRIPTION"

PURCHASE_NONE = "NONE"
PURCHASE_ACTIVE = "ACTIVE"
PURCHASE_ON_HOLD = "ON_HOLD"
PURCHASE_ENDED = "ENDED"
PURCHASE_STATUS_VALUES: tuple[str, ...] = (
    PURCHASE_NONE,
    PURCHASE_ACTIVE,
    PURCHASE_ON_HOLD,
    PURCHASE_ENDED,
)

#: Notification stages, recorded so a sweep that runs every 15 minutes sends
#: each message once.
STAGE_GRACE_STARTED = "GRACE_STARTED"
STAGE_GRACE_ENDING = "GRACE_ENDING"
STAGE_HALTED = "HALTED"
STAGE_RESTORED = "RESTORED"


class OrganizationAddon(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organization_addons"

    __table_args__ = (
        CheckConstraint(
            "addon_key IN ('addon.custom_domain', 'addon.warehouse_sync')",
            name="ck_organization_addons_key_known",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'GRACE', 'LAPSED')",
            name="ck_organization_addons_status_known",
        ),
        CheckConstraint(
            "granted_by IS NULL OR granted_by IN ('TIER', 'SUBSCRIPTION')",
            name="ck_organization_addons_granted_by_known",
        ),
        CheckConstraint(
            "purchase_status IN ('NONE', 'ACTIVE', 'ON_HOLD', 'ENDED')",
            name="ck_organization_addons_purchase_status_known",
        ),
        CheckConstraint(
            "gateway IS NULL OR gateway IN ('STRIPE', 'DODO')",
            name="ck_organization_addons_gateway_known",
        ),
        CheckConstraint(
            "status <> 'GRACE' OR (grace_started_at IS NOT NULL "
            "AND grace_ends_at IS NOT NULL AND grace_ends_at > grace_started_at)",
            name="ck_organization_addons_grace_has_window",
        ),
        CheckConstraint(
            "status <> 'LAPSED' OR lapsed_at IS NOT NULL",
            name="ck_organization_addons_lapsed_has_timestamp",
        ),
        CheckConstraint(
            "purchase_status <> 'ON_HOLD' OR purchase_grace_ends_at IS NOT NULL",
            name="ck_organization_addons_on_hold_has_grace",
        ),
        CheckConstraint(
            "purchase_status = 'NONE' OR (gateway IS NOT NULL "
            "AND gateway_subscription_id IS NOT NULL)",
            name="ck_organization_addons_purchase_names_subscription",
        ),
        CheckConstraint(
            "gateway_state_version >= 0",
            name="ck_organization_addons_state_version_non_negative",
        ),
        Index(
            "uq_organization_addons_org_key",
            "organization_id",
            "addon_key",
            unique=True,
        ),
        Index(
            "uq_organization_addons_gateway_subscription",
            "gateway",
            "gateway_subscription_id",
            unique=True,
            postgresql_where=text("gateway_subscription_id IS NOT NULL"),
        ),
        Index(
            "ix_organization_addons_grace_due",
            "grace_ends_at",
            postgresql_where=text("status = 'GRACE'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    addon_key: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )
    granted_by: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
        doc=(
            "The grant source last observed. NULL when the row was opened for "
            "resources that predate add-on gating and never had a grant."
        ),
    )

    purchase_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NONE'")
    )
    purchase_grace_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    gateway_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    gateway_state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
        doc="Epoch micros at fetch issue; purchase writes are guarded on it.",
    )

    grace_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lapsed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    halted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_notified_stage: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OrganizationAddon org={self.organization_id} {self.addon_key} "
            f"status={self.status} purchase={self.purchase_status}>"
        )


__all__ = [
    "ADDON_STATUS_ACTIVE",
    "ADDON_STATUS_GRACE",
    "ADDON_STATUS_LAPSED",
    "ADDON_STATUS_VALUES",
    "GRANTED_BY_SUBSCRIPTION",
    "GRANTED_BY_TIER",
    "OrganizationAddon",
    "PURCHASE_ACTIVE",
    "PURCHASE_ENDED",
    "PURCHASE_NONE",
    "PURCHASE_ON_HOLD",
    "PURCHASE_STATUS_VALUES",
    "STAGE_GRACE_ENDING",
    "STAGE_GRACE_STARTED",
    "STAGE_HALTED",
    "STAGE_RESTORED",
]
