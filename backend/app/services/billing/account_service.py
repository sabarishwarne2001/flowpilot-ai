"""ARCH-15 Step 15.3 — `billing_accounts` (F5, F7).

The organization-to-Stripe-customer mapping, and the currency assertion that
stops two currencies being summed as though they were one.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.price_book import PriceBook
from app.models.user import User
from app.services.billing import stripe_gateway

logger = logging.getLogger("app.services.billing.account")


class BillingAccountError(Exception):
    """Base class for billing-account refusals."""


class BillingAccountNotFoundError(BillingAccountError):
    """No billing account exists for this organization or customer."""


class CurrencyMismatchError(BillingAccountError):
    """F7. The account currency and the price book in force disagree."""


def _normalise_email(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if "@" not in cleaned[1:]:
        raise BillingAccountError(f"{value!r} is not a usable billing address.")
    if len(cleaned) > 320:
        raise BillingAccountError("Billing address exceeds 320 characters.")
    return cleaned


# ============================================================================
# Reads
# ============================================================================


def get_for_organization(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[BillingAccount]:
    return db.execute(
        select(BillingAccount).where(
            BillingAccount.organization_id == organization_id
        )
    ).scalar_one_or_none()


def get_by_customer_id(
    db: Session, *, stripe_customer_id: str
) -> Optional[BillingAccount]:
    return db.execute(
        select(BillingAccount).where(
            BillingAccount.stripe_customer_id == stripe_customer_id
        )
    ).scalar_one_or_none()


def require_for_organization(
    db: Session, *, organization_id: uuid.UUID
) -> BillingAccount:
    account = get_for_organization(db, organization_id=organization_id)
    if account is None:
        raise BillingAccountNotFoundError(
            f"Organization {organization_id} has no billing account."
        )
    return account


def has_billing_account(db: Session, *, organization_id: uuid.UUID) -> bool:
    """Cheap existence check, indexed.

    Used by the seat hooks on the membership hot path: a tenant that has never
    paid should not put rows in the outbox every time somebody joins.
    """
    return (
        db.execute(
            select(BillingAccount.id)
            .where(BillingAccount.organization_id == organization_id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


# ============================================================================
# F7 — the single-currency assertion
# ============================================================================


def currency_in_force(db: Session, *, at: Optional[datetime] = None) -> Optional[str]:
    """The currency of the published price book covering `at`."""
    moment = at or datetime.now(timezone.utc)
    row = db.execute(
        select(PriceBook.currency)
        .where(
            PriceBook.is_active.is_(True),
            PriceBook.published_at.is_not(None),
            PriceBook.effective_from <= moment,
            (PriceBook.effective_to.is_(None)) | (PriceBook.effective_to > moment),
        )
        .order_by(PriceBook.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(row).upper() if row else None


def assert_currency(db: Session, *, currency: str) -> str:
    """Refuse a currency the price book in force does not use.

    ARCH-15 does not add multi-currency support; it adds a *refusal*. The
    failure being prevented is arithmetic, not policy: `cost_micros` and
    `max_cost_micros` are bare integers with no currency attached, so an
    invoice summing EUR-priced and USD-priced micros produces a number that
    looks entirely reasonable and is wrong.

    A deployment with no published book yet is allowed through — that is
    ARCH-14 Gate 14.1's problem, and refusing here would make tenants
    uncreatable in the window between a schema deploy and the first publish.
    """
    normalised = (currency or "").strip().upper()
    if len(normalised) != 3:
        raise CurrencyMismatchError(
            f"{currency!r} is not a 3-letter ISO-4217 code."
        )

    book_currency = currency_in_force(db)
    if book_currency is None:
        return normalised

    if normalised != book_currency:
        raise CurrencyMismatchError(
            f"Billing account currency {normalised} differs from the price "
            f"book in force ({book_currency}). ARCH-15 refuses to mix "
            "currencies rather than sum them silently."
        )
    return normalised


# ============================================================================
# Writes
# ============================================================================


def default_billing_email(db: Session, *, organization_id: uuid.UUID) -> str:
    """The address a new billing account starts with.

    Seeded from the current owner and then **frozen**: it is a column on the
    organization's billing account, not a lookup through whoever holds OWNER
    today. F4 is the reason — an ownership transfer must not silently move a
    payment instrument to somebody who never agreed to pay for anything.
    """
    email = db.execute(
        select(User.email)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
        .order_by(OrganizationMember.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    if not email:
        raise BillingAccountError(
            f"Organization {organization_id} has no active owner to seed a "
            "billing address from."
        )
    return _normalise_email(str(email))


def ensure_billing_account(
    db: Session,
    *,
    organization_id: uuid.UUID,
    billing_email: Optional[str] = None,
    currency: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
    create_remote: bool = True,
) -> BillingAccount:
    """Get or create the account, creating the Stripe customer if needed.

    `stripe_customer_id` is accepted so the reconciler can adopt a customer
    Stripe already created — a Checkout session creates the customer before we
    ever see an event about it, and creating a second one here would leave two
    customers for one tenant, one of which nobody is watching.
    """
    existing = get_for_organization(db, organization_id=organization_id)
    if existing is not None:
        return existing

    organization = db.execute(
        select(Organization).where(Organization.id == organization_id)
    ).scalar_one_or_none()
    if organization is None:
        raise BillingAccountError(f"Organization {organization_id} does not exist.")

    email = _normalise_email(
        billing_email or default_billing_email(db, organization_id=organization_id)
    )
    resolved_currency = assert_currency(
        db,
        currency=currency
        or currency_in_force(db)
        or settings.BILLING_DEFAULT_CURRENCY,
    )

    customer_id = stripe_customer_id
    if customer_id is None:
        if not create_remote:
            raise BillingAccountError(
                "No Stripe customer id supplied and remote creation is "
                "disabled."
            )
        snapshot = stripe_gateway.get_gateway().create_customer(
            organization_id=organization_id,
            email=email,
            name=organization.legal_name or organization.name,
            currency=resolved_currency,
        )
        customer_id = snapshot.id

    account = BillingAccount(
        organization_id=organization_id,
        stripe_customer_id=customer_id,
        currency=resolved_currency,
        billing_email=email,
    )
    db.add(account)
    db.flush()

    logger.info(
        "billing_account.created",
        extra={
            "organization_id": str(organization_id),
            "stripe_customer_id": customer_id,
            "currency": resolved_currency,
        },
    )
    return account


def update_billing_email(
    db: Session,
    *,
    organization_id: uuid.UUID,
    billing_email: str,
    push_to_stripe: bool = True,
) -> BillingAccount:
    """Change where invoices go. An explicit act, never a side effect.

    F4 in one function: ownership transfer does **not** call this. A transfer
    changes who administers the tenant; it does not, on its own, change who
    receives and pays the bills. Making it implicit would move a payment
    instrument onto somebody who agreed to become an owner, not a payer.
    """
    account = require_for_organization(db, organization_id=organization_id)
    email = _normalise_email(billing_email)

    if email == account.billing_email:
        return account

    previous = account.billing_email
    account.billing_email = email
    db.flush()

    if push_to_stripe:
        # Deliberately after the local flush and outside any assumption of
        # success: if Stripe is unreachable the local row is still correct and
        # the next reconcile re-asserts it. The reverse ordering would leave
        # Stripe holding an address our database never accepted.
        try:
            stripe_gateway.get_gateway().update_customer_email(
                customer_id=account.stripe_customer_id, email=email
            )
        except stripe_gateway.StripeGatewayError as exc:
            logger.warning(
                "billing_account.email_push_failed",
                extra={
                    "organization_id": str(organization_id),
                    "error": str(exc),
                },
            )

    logger.info(
        "billing_account.email_changed",
        extra={
            "organization_id": str(organization_id),
            "previous": previous,
            "current": email,
        },
    )
    return account


def adopt_customer(
    db: Session,
    *,
    organization_id: uuid.UUID,
    stripe_customer_id: str,
    billing_email: Optional[str] = None,
    currency: Optional[str] = None,
) -> BillingAccount:
    """Bind an existing Stripe customer to an organization.

    The reconciler's entry point. Idempotent, and it refuses to re-point an
    account at a different customer: two customer ids for one tenant means one
    of them is billing somebody nobody is watching, and quietly overwriting
    the mapping loses the evidence of which.
    """
    account = get_for_organization(db, organization_id=organization_id)
    if account is None:
        return ensure_billing_account(
            db,
            organization_id=organization_id,
            billing_email=billing_email,
            currency=currency,
            stripe_customer_id=stripe_customer_id,
            create_remote=False,
        )

    if account.stripe_customer_id != stripe_customer_id:
        raise BillingAccountError(
            f"Organization {organization_id} is already bound to Stripe "
            f"customer {account.stripe_customer_id}; refusing to re-point it "
            f"at {stripe_customer_id}. One of the two is charging a card "
            "nobody is reconciling — resolve it in the Stripe dashboard."
        )
    return account


def mark_delinquent(
    db: Session, *, organization_id: uuid.UUID, at: Optional[datetime] = None
) -> None:
    """Set `delinquent_since` once and never move it forward.

    15.8 owns the dunning schedule; this is the write it needs. Idempotent by
    the `IS NULL` predicate, because dunning steps are retried and a
    delinquency clock that resets on every retry never reaches its terminal
    step.
    """
    db.execute(
        update(BillingAccount)
        .where(
            BillingAccount.organization_id == organization_id,
            BillingAccount.delinquent_since.is_(None),
        )
        .values(
            delinquent_since=at or func.now(),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def clear_delinquency(db: Session, *, organization_id: uuid.UUID) -> None:
    db.execute(
        update(BillingAccount)
        .where(BillingAccount.organization_id == organization_id)
        .values(delinquent_since=None, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


__all__ = [
    "BillingAccountError",
    "BillingAccountNotFoundError",
    "CurrencyMismatchError",
    "adopt_customer",
    "assert_currency",
    "clear_delinquency",
    "currency_in_force",
    "default_billing_email",
    "ensure_billing_account",
    "get_by_customer_id",
    "get_for_organization",
    "has_billing_account",
    "mark_delinquent",
    "require_for_organization",
    "update_billing_email",
]
