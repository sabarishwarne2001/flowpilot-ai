"""ARCH-15 — billing response schemas.

Tranches 1 and 2 expose exactly one endpoint (the webhook), so this module is
small on purpose. The read models are here because the gate suites assert
against them and because 15.7's endpoints will need them unchanged; adding the
routes early would put an owner-only surface in a tranche whose SEC-1
dependency has not landed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StripeWebhookAck(BaseModel):
    """The 200 body Stripe receives.

    Deliberately says nothing about what will happen next. A webhook response
    that reported reconcile results would be a response that had to wait for
    them, and waiting is the thing this endpoint exists not to do.
    """

    received: bool = True
    duplicate: bool = Field(
        default=False,
        description=(
            "True when this `event.id` was already held. Still a 200: a "
            "non-2xx would make Stripe retry a delivery that succeeded."
        ),
    )


class BillingAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    stripe_customer_id: str
    currency: str
    billing_email: str
    tax_id: Optional[str] = None
    delinquent_since: Optional[datetime] = None
    created_at: datetime


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stripe_subscription_id: str
    status: str
    quota_tier_key: str
    quota_tier_id: uuid.UUID
    price_book_id: uuid.UUID
    seats_purchased: int
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    last_reconciled_at: datetime


class SeatStateRead(BaseModel):
    """Belief versus truth, side by side.

    Both numbers are exposed rather than one reconciled figure, because the
    interesting question when they differ is *which* is wrong, and a single
    number cannot answer it.
    """

    organization_id: uuid.UUID
    seats_billable: int = Field(description="Derived from ACTIVE memberships.")
    seats_purchased: int = Field(description="What Stripe currently believes.")
    delta: int
    direction: str


class SeatSyncResult(BaseModel):
    organization_id: uuid.UUID
    outcome: str
    seats_billable: Optional[int] = None
    seats_now_purchased: Optional[int] = None
    proration_behavior: Optional[str] = None


class InboundEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stripe_event_id: str
    event_type: str
    status: str
    attempts: int
    received_at: datetime
    processed_at: Optional[datetime] = None
    last_error: Optional[str] = None


__all__ = [
    "BillingAccountRead",
    "InboundEventRead",
    "SeatStateRead",
    "SeatSyncResult",
    "StripeWebhookAck",
    "SubscriptionRead",
]
