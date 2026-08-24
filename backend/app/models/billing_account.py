"""ARCH-15 Step 15.3 — `billing_accounts` (F5, F7).

WHY NOT `organizations.stripe_customer_id`
==========================================

The obvious move is a nullable column on the tenancy root. Resist it:

1. Billing state has a different lifecycle and a *different access-control
   profile* from the tenancy root. `RequireOrgAdmin` may read a bill;
   `RequireOrgOwner` alone may change a payment instrument. Columns with
   different authorisation rules do not belong in the same row.
2. Every ARCH-01 query would carry billing columns it does not want.
3. Every billing change would touch the table ARCH-01 stabilised, and ARCH-01
   is the table whose stability the entire isolation suite rests on.

One row per organization, `organization_id` UNIQUE, `ON DELETE RESTRICT`.

WHY `RESTRICT` AND NOT `CASCADE`
================================

Deleting an organization that still has a Stripe customer must fail loudly.
The alternative is an orphaned Stripe subscription that keeps charging a card
for a tenant that no longer exists — which is a chargeback, a support ticket,
and in several jurisdictions a regulatory problem, in that order.

F7 — THE SINGLE-CURRENCY ASSERTION
==================================

`price_books.currency` exists. `spend_limits.max_cost_micros` and
`usage_events.cost_micros` are bare integers with no currency at all. Today
everything is USD and the ambiguity is harmless. It stops being harmless the
day someone publishes a EUR price book: an invoice would sum micros priced in
two currencies and nothing would notice, because both are integers.

ARCH-15 does not add multi-currency. It adds a **refusal**: `currency` here
must equal the currency of the price book in force, enforced by
`trg_billing_accounts_currency_matches_book`. The day the second currency
arrives, the system stops rather than silently mixing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.subscription import Subscription


class BillingAccount(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "billing_accounts"

    __table_args__ = (
        CheckConstraint(
            "length(currency) = 3", name="ck_billing_accounts_currency_iso4217"
        ),
        CheckConstraint(
            "currency = upper(currency)", name="ck_billing_accounts_currency_upper"
        ),
        CheckConstraint(
            "length(stripe_customer_id) > 0",
            name="ck_billing_accounts_customer_id_not_blank",
        ),
        # Normalised on write by `account_service._normalise_email`. The CHECK
        # is what stops a psql session from creating the second casing of an
        # address the UNIQUE-per-org invariant assumes is singular.
        CheckConstraint(
            "billing_email = lower(billing_email)",
            name="ck_billing_accounts_billing_email_lowercase",
        ),
        CheckConstraint(
            "position('@' in billing_email) > 1",
            name="ck_billing_accounts_billing_email_shaped",
        ),
        Index(
            "uq_billing_accounts_organization_id",
            "organization_id",
            unique=True,
        ),
        Index(
            "uq_billing_accounts_stripe_customer_id",
            "stripe_customer_id",
            unique=True,
        ),
        Index(
            "ix_billing_accounts_delinquent",
            text("delinquent_since DESC"),
            postgresql_where=text("delinquent_since IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    stripe_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default=text("'USD'"),
        doc="F7. Must equal the currency of the price book in force.",
    )

    billing_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        doc=(
            "F4. Follows the organization, not the owner. An ownership "
            "transfer does not move it, because a transfer must not move a "
            "payment instrument to somebody who never agreed to pay. "
            "Changing it is an explicit owner action."
        ),
    )

    tax_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    delinquent_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Set by 15.8's terminal dunning step. Never set by a reconcile.",
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", foreign_keys=[organization_id], lazy="joined"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription",
        back_populates="billing_account",
        # No delete-orphan: a subscription row outliving its account would be
        # a bug, but silently deleting historical subscriptions to satisfy an
        # ORM cascade would be a worse one. `RESTRICT` on the FK is the rule.
        passive_deletes=True,
    )

    @property
    def is_delinquent(self) -> bool:
        return self.delinquent_since is not None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BillingAccount org={self.organization_id} "
            f"{self.stripe_customer_id} {self.currency}>"
        )


__all__ = ["BillingAccount"]