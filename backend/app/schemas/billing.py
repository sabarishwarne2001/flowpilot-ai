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
from typing import Any, Optional

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


class SeatPriceBookResponse(BaseModel):
    """The seat figures the JIT policy panel is allowed to render.

    ARCH-24 Tranche 4. Two nullable money fields, each nullable for a different
    reason, each carrying its own provenance discriminator:

      unit_price_micros / price_source
          NULL means the subscription's pinned price book has no seat entry.
          A configuration fault, not a free seat.

      proration_micros / proration_source
          NULL means Stripe could not be reached. The real figure is whatever
          Stripe eventually invoices; we decline to guess it.

    `24-G6` asserts these are sourced rather than computed, which is why the
    provenance fields are required rather than optional: a payload that cannot
    say where a number came from should not be renderable at all.

    The frontend must not do arithmetic on any of this. Rendering
    `unit_price_micros * seats` as a total would reintroduce, in TypeScript,
    exactly the local-proration drift ARCH-15 removed from Python.
    """

    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID

    seats_current: int = Field(
        ..., description="Seats currently purchased on the live subscription."
    )
    seats_after: int = Field(
        ..., description="Seats after the prospective change."
    )

    unit_price_micros: Optional[int] = Field(
        None,
        description=(
            "Seat unit price from the subscription's PINNED price book, in "
            "micros. NULL when unpriced \u2014 never 0."
        ),
    )
    unit: Optional[str] = None
    currency: str = "USD"
    price_book_id: Optional[uuid.UUID] = None
    price_book_version: Optional[int] = None
    price_source: str = Field(
        ..., description="PRICE_BOOK or UNPRICED."
    )

    proration_micros: Optional[int] = Field(
        None,
        description=(
            "Prorated amount for the change, in micros, AS REPORTED BY STRIPE. "
            "NULL when unavailable \u2014 never 0, never locally derived."
        ),
    )
    proration_source: str = Field(
        ..., description="STRIPE_PREVIEW or UNAVAILABLE."
    )
    proration_unavailable_reason: Optional[str] = None

    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None

    @classmethod
    def build(cls, disclosure: Any) -> "SeatPriceBookResponse":
        return cls(
            organization_id=disclosure.organization_id,
            seats_current=disclosure.seats_current,
            seats_after=disclosure.seats_after,
            unit_price_micros=disclosure.unit_price_micros,
            unit=disclosure.unit,
            currency=disclosure.currency,
            price_book_id=disclosure.price_book_id,
            price_book_version=disclosure.price_book_version,
            price_source=disclosure.price_source,
            proration_micros=disclosure.proration_micros,
            proration_source=disclosure.proration_source,
            proration_unavailable_reason=(
                disclosure.proration_unavailable_reason
            ),
            period_start=disclosure.period_start,
            period_end=disclosure.period_end,
        )


__all__ = [
    "BillingAccountRead",
    "SeatPriceBookResponse",
    "InboundEventRead",
    "SeatStateRead",
    "SeatSyncResult",
    "StripeWebhookAck",
    "SubscriptionRead",
]