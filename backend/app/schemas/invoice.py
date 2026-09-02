"""ARCH-15 Tranches 3/4 — billing API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_number: int
    kind: str
    description: str
    quantity: Decimal
    unit: str
    unit_price_micros: Decimal
    amount_micros: int
    limit_key: Optional[str] = None
    event_type: Optional[str] = None
    included_quantity: Optional[Decimal] = None
    estimated_quantity: Optional[Decimal] = Field(
        default=None,
        description=(
            "How much of this line's quantity was an estimate flagged by a "
            "provider disconnect. Disclosed rather than hidden: a customer is "
            "entitled to know before they ask, not during a dispute."
        ),
    )
    usage_event_count: Optional[int] = None
    price_book_entry_id: Optional[uuid.UUID] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind_value(cls, v: Any) -> str:
        return v.value if hasattr(v, "value") else str(v)


class SubscriptionBrief(BaseModel):
    id: uuid.UUID
    stripe_subscription_id: str
    status: str
    quota_tier_key: str
    quota_tier_id: uuid.UUID
    price_book_id: uuid.UUID
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    status: str
    currency: str
    period_start: datetime
    period_end: datetime
    subtotal_micros: int
    tax_micros: int
    total_micros: int
    amount_paid_micros: int
    seats_billed: int
    finalized_at: Optional[datetime] = None
    stripe_invoice_id: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def _status_value(cls, v: Any) -> str:
        return v.value if hasattr(v, "value") else str(v)

    @staticmethod
    def subscription_view(subscription: Any) -> "SubscriptionBrief":
        return SubscriptionBrief(
            id=subscription.id,
            stripe_subscription_id=subscription.stripe_subscription_id,
            status=(
                subscription.status.value
                if hasattr(subscription.status, "value")
                else str(subscription.status)
            ),
            quota_tier_key=subscription.quota_tier_key,
            quota_tier_id=subscription.quota_tier_id,
            price_book_id=subscription.price_book_id,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            cancel_at_period_end=bool(subscription.cancel_at_period_end),
        )


class InvoiceListResponse(BaseModel):
    organization_id: uuid.UUID
    invoices: list[InvoiceSummary]
    count: int


class InvoiceDetailResponse(BaseModel):
    invoice: InvoiceSummary
    line_items: list[InvoiceLineItemRead]
    content_digest: str
    digest_matches: bool
    assembly_notes: Optional[dict[str, Any]] = None

    @classmethod
    def build(cls, *, invoice: Any, digest_matches: bool) -> "InvoiceDetailResponse":
        return cls(
            invoice=InvoiceSummary.model_validate(invoice),
            line_items=[
                InvoiceLineItemRead.model_validate(line)
                for line in sorted(
                    invoice.line_items, key=lambda item: item.line_number
                )
            ],
            content_digest=invoice.content_digest,
            digest_matches=digest_matches,
            assembly_notes=invoice.assembly_notes,
        )


class InvoiceProvenance(BaseModel):
    price_book_id: uuid.UUID
    price_book_version: int
    price_book_currency: str
    quota_tier_id: uuid.UUID
    quota_tier_key: str
    quota_tier_version: int


class InvoiceIntegrity(BaseModel):
    digest_matches: bool
    stored_digest: str
    recomputed_digest: str
    arithmetic_ok: bool
    arithmetic_failures: list[dict[str, Any]] = Field(default_factory=list)
    reproducible: bool


class InvoiceReproductionResponse(BaseModel):
    """The A9 artifact.

    Carries the provenance *by version number* and not only by id, because the
    person reading this during a dispute is comparing it against a contract
    that says "v3", not against a UUID.
    """

    invoice: dict[str, Any]
    provenance: InvoiceProvenance
    integrity: InvoiceIntegrity
    lines: list[dict[str, Any]]


class SubscriptionStateResponse(BaseModel):
    organization_id: uuid.UUID
    has_billing_account: bool
    currency: Optional[str] = None
    billing_email: Optional[str] = None
    delinquent_since: Optional[datetime] = None
    subscription: Optional[SubscriptionBrief] = None
    seats_billable: int
    seats_purchased: int
    seat_drift_delta: int = Field(
        description=(
            "billable − purchased. Exposed rather than reconciled away: when "
            "the two differ, the useful question is which one is wrong."
        )
    )
    access_state: str


class BillingAccessResponse(BaseModel):
    organization_id: uuid.UUID
    access_state: str
    writes_allowed: bool
    reads_allowed: bool
    export_allowed: bool = Field(
        description="Always true. Holding data hostage to collect a debt is "
        "not a collections strategy."
    )
    data_retained: bool = Field(
        description="Always true. Dunning never deletes customer data."
    )
    dunning_steps_applied: list[str]
    next_dunning_step: Optional[str] = None


class CheckoutSessionRequest(BaseModel):
    quota_tier_key: str = Field(min_length=1, max_length=32)
    seats: int = Field(default=1, ge=1, le=10_000)
    price_id: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PortalSessionRequest(BaseModel):
    return_url: Optional[str] = None


class SeatSyncRequest(BaseModel):
    reason: str = Field(default="owner_requested", max_length=64)
    force: bool = False


class EphemeralSessionResponse(BaseModel):
    """A minted session URL.

    Returned once and never stored — not in a table, not in a cache, not in an
    audit `details` blob. See `portal_service` for why a portal URL is treated
    as a credential rather than a link.
    """

    url: str
    kind: str
    expires_at: Optional[datetime] = None


__all__ = [
    "BillingAccessResponse",
    "CheckoutSessionRequest",
    "EphemeralSessionResponse",
    "InvoiceDetailResponse",
    "InvoiceIntegrity",
    "InvoiceLineItemRead",
    "InvoiceListResponse",
    "InvoiceProvenance",
    "InvoiceReproductionResponse",
    "InvoiceSummary",
    "PortalSessionRequest",
    "SeatSyncRequest",
    "SubscriptionBrief",
    "SubscriptionStateResponse",
]
