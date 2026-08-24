"""ARCH-15 Step 15.7 — Checkout and the Customer Portal (F6)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token_claims
from app.models.billing_account import BillingAccount
from app.services.billing import account_service, stripe_gateway

logger = logging.getLogger("app.services.billing.portal")


class PortalError(Exception):
    """Base class for portal and checkout refusals."""


class ReauthenticationRequiredError(PortalError):
    """F6. The caller's authentication is not fresh enough to mint a portal URL."""


class CheckoutConfigurationError(PortalError):
    """Checkout cannot be started because pricing is not configured."""


@dataclass(frozen=True)
class EphemeralSession:
    url: str
    expires_at: Optional[datetime]
    kind: str
    stripe_session_id: Optional[str] = None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EphemeralSession {self.kind} id={self.stripe_session_id} "
            "url=[redacted]>"
        )


def _issued_at_from_claims(token: str) -> Optional[datetime]:
    claims = decode_access_token_claims(token)
    if claims is None:
        return None
    issued = claims.issued_at
    if issued is None:
        return None
    return issued if issued.tzinfo else issued.replace(tzinfo=timezone.utc)


def bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None
    scheme, _, credential = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        return None
    return credential.strip()


def assert_recent_authentication(
    *,
    authorization_header: Optional[str] = None,
    issued_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> datetime:
    if not settings.BILLING_REAUTH_REQUIRED:
        logger.warning(
            "billing.reauth_check_disabled",
            extra={"window_seconds": settings.BILLING_REAUTH_WINDOW_S},
        )
        return now or datetime.now(timezone.utc)

    moment = now or datetime.now(timezone.utc)
    resolved = issued_at

    if resolved is None:
        token = bearer_token(authorization_header)
        if token is None:
            raise ReauthenticationRequiredError(
                "Minting a billing portal session requires an interactive "
                "session token. API keys cannot mint one: a long-lived "
                "programmatic credential is not fresh authentication."
            )
        resolved = _issued_at_from_claims(token)

    if resolved is None:
        raise ReauthenticationRequiredError(
            "Could not establish when this session was authenticated. "
            "Re-authenticate and retry."
        )

    window = timedelta(seconds=int(settings.BILLING_REAUTH_WINDOW_S))
    age = moment - resolved

    if age > window:
        raise ReauthenticationRequiredError(
            f"This session was authenticated {int(age.total_seconds())}s ago, "
            f"outside the {int(window.total_seconds())}s window required to "
            "manage payment methods. Re-authenticate and retry."
        )

    if age < -timedelta(seconds=60):
        raise ReauthenticationRequiredError(
            "Session token issue time is in the future; refusing."
        )

    return resolved


def create_portal_session(
    db: Session,
    *,
    organization_id: uuid.UUID,
    return_url: Optional[str] = None,
    authorization_header: Optional[str] = None,
    issued_at: Optional[datetime] = None,
) -> EphemeralSession:
    assert_recent_authentication(
        authorization_header=authorization_header, issued_at=issued_at
    )

    account = account_service.require_for_organization(
        db, organization_id=organization_id
    )

    session = stripe_gateway.get_gateway().create_portal_session(
        customer_id=account.stripe_customer_id,
        return_url=return_url or settings.BILLING_PORTAL_RETURN_URL,
    )

    logger.info(
        "billing.portal_session_minted",
        extra={
            "organization_id": str(organization_id),
            "stripe_customer_id": account.stripe_customer_id,
        },
    )
    return session


def create_checkout_session(
    db: Session,
    *,
    organization_id: uuid.UUID,
    quota_tier_key: str,
    seats: int,
    price_id: Optional[str] = None,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> EphemeralSession:
    resolved_price = price_id or settings.BILLING_SEAT_PRICE_ID
    if not resolved_price:
        raise CheckoutConfigurationError(
            "No Stripe price configured. Set BILLING_SEAT_PRICE_ID or pass "
            "price_id explicitly; refusing to start a checkout that cannot "
            "name what it is selling."
        )
    if seats < 1:
        raise CheckoutConfigurationError("A subscription needs at least one seat.")

    account = account_service.ensure_billing_account(
        db, organization_id=organization_id
    )

    session = stripe_gateway.get_gateway().create_checkout_session(
        customer_id=account.stripe_customer_id,
        price_id=resolved_price,
        seats=int(seats),
        organization_id=organization_id,
        quota_tier_key=quota_tier_key,
        success_url=success_url or settings.BILLING_CHECKOUT_SUCCESS_URL,
        cancel_url=cancel_url or settings.BILLING_CHECKOUT_CANCEL_URL,
    )

    logger.info(
        "billing.checkout_session_created",
        extra={
            "organization_id": str(organization_id),
            "quota_tier_key": quota_tier_key,
            "seats": int(seats),
            "stripe_session_id": session.stripe_session_id,
        },
    )
    return session


def billing_account_summary(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[BillingAccount]:
    return account_service.get_for_organization(db, organization_id=organization_id)


__all__ = [
    "CheckoutConfigurationError",
    "EphemeralSession",
    "PortalError",
    "ReauthenticationRequiredError",
    "assert_recent_authentication",
    "bearer_token",
    "billing_account_summary",
    "create_checkout_session",
    "create_portal_session",
]