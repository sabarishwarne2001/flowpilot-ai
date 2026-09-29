"""ARCH50-S1:schemas — RevOps API shapes (operator console and the tenant's billing additions)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Currency = Literal["USD", "INR"]
PlanInterval = Literal["month", "year"]
ContractInterval = Literal["month", "quarter", "year"]
PromoDuration = Literal["ONCE", "REPEATING", "FOREVER"]
EndReason = Literal["TERM_ENDED", "CANCELLED", "SUPERSEDED", "NON_PAYMENT"]


class PriceBookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
    currency: Currency
    notes: Optional[str] = Field(default=None, max_length=500)


class PriceEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier_key: str = Field(min_length=1, max_length=32)
    interval: PlanInterval
    unit_amount: int = Field(ge=0, le=10**11, description="minor units (cents / paise)")
    gateway_price_id: Optional[str] = Field(default=None, max_length=255)


class PriceEntryOut(BaseModel):
    id: uuid.UUID
    tier_key: str
    billing_interval: str
    unit_amount_micros: int
    gateway_price_id: Optional[str] = None


class PriceBookOut(BaseModel):
    id: uuid.UUID
    code: str
    currency: str
    status: str
    notes: Optional[str] = None
    content_digest: Optional[str] = None
    published_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    created_at: datetime
    entries: list[PriceEntryOut] = []


class PriceBookSummary(BaseModel):
    id: uuid.UUID
    code: str
    currency: str
    status: str
    notes: Optional[str] = None
    content_digest: Optional[str] = None
    published_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    created_at: datetime
    entries: int


class PromoCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    description: Optional[str] = Field(default=None, max_length=200)
    percent_off: Optional[float] = Field(default=None, gt=0, le=100)
    amount_off: Optional[int] = Field(default=None, gt=0, description="minor units")
    currency: Optional[Currency] = None
    duration: PromoDuration = "ONCE"
    duration_in_months: Optional[int] = Field(default=None, ge=1, le=36)
    max_redemptions: Optional[int] = Field(default=None, ge=1)
    redeem_by: Optional[datetime] = None
    applies_to_tiers: Optional[list[str]] = None
    applies_to_intervals: Optional[list[PlanInterval]] = None
    first_subscription_only: bool = False
    gateway_coupon_id: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _shape(self) -> "PromoCodeIn":
        if (self.percent_off is None) == (self.amount_off is None):
            raise ValueError("give exactly one of percent_off and amount_off")
        if self.amount_off is not None and self.currency is None:
            raise ValueError("an amount off needs its currency")
        if (self.duration == "REPEATING") != (self.duration_in_months is not None):
            raise ValueError("duration_in_months is required for REPEATING and only for it")
        return self


class PromoCodeOut(BaseModel):
    id: uuid.UUID
    code: str
    description: Optional[str] = None
    percent_off: Optional[float] = None
    amount_off_micros: Optional[int] = None
    currency: Optional[str] = None
    duration: str
    duration_in_months: Optional[int] = None
    max_redemptions: Optional[int] = None
    times_redeemed: int
    redeem_by: Optional[datetime] = None
    applies_to_tiers: Optional[list[str]] = None
    applies_to_intervals: Optional[list[str]] = None
    first_subscription_only: bool
    gateway_coupon_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    redemptions: dict[str, int] = {}


class PromoActiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class ContractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    contract_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9/_-]{2,31}$")
    tier_key: str = Field(min_length=1, max_length=32)
    seats: int = Field(ge=1, le=1_000_000)
    currency: Currency
    billing_interval: ContractInterval
    amount_per_period: int = Field(ge=0, le=10**12, description="minor units")
    tax_rate_bps: int = Field(default=0, ge=0, le=10000)
    term_start: date
    term_end: date
    payment_terms_days: int = Field(default=30, ge=0, le=120)
    po_number: Optional[str] = Field(default=None, max_length=64)
    billing_email: Optional[str] = Field(default=None, max_length=254)
    promo_code: Optional[str] = Field(default=None, max_length=32)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ContractInvoiceOut(BaseModel):
    id: uuid.UUID
    invoice_number: str
    period_start: date
    period_end: date
    currency: str
    subtotal_micros: int
    discount_micros: int
    tax_micros: int
    total_micros: int
    status: str
    issued_at: datetime
    due_at: datetime
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    void_reason: Optional[str] = None
    overdue: bool = False


class ContractOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: Optional[str] = None
    contract_number: str
    tier_key: str
    seats: int
    currency: str
    billing_interval: str
    amount_per_period_micros: int
    tax_rate_bps: int
    term_start: date
    term_end: date
    payment_terms_days: int
    po_number: Optional[str] = None
    billing_email: Optional[str] = None
    status: str
    promo_code_id: Optional[uuid.UUID] = None
    activated_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    end_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    invoices: list[ContractInvoiceOut] = []


class ContractSummary(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: Optional[str] = None
    contract_number: str
    tier_key: str
    seats: int
    currency: str
    billing_interval: str
    amount_per_period_micros: int
    term_start: date
    term_end: date
    status: str
    open_invoices: int
    overdue_invoices: int
    created_at: datetime


class ContractEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: EndReason


class InvoicePayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1, max_length=128)
    paid_at: Optional[datetime] = None


class InvoiceVoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=200)


class RevenueMetricsOut(BaseModel):
    as_of: datetime
    compare_days: int
    currencies: dict[str, dict[str, Any]]
    receivables: dict[str, dict[str, int]]
    unpriced_subscriptions: list[dict[str, Any]]
    promo_redemptions: dict[str, dict[str, int]]
    snapshots: list[dict[str, Any]]


class SweepOut(BaseModel):
    reservations_expired: int
    redemptions_confirmed: int
    contracts_ended: int
    invoices_issued: int
    snapshots_written: int


class PromoQuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=32)
    quota_tier_key: str = Field(min_length=1, max_length=32)
    interval: PlanInterval = "month"
    currency: Currency = "USD"
    seats: int = Field(default=1, ge=1, le=100_000)


class PromoQuoteOut(BaseModel):
    code: str
    valid: bool
    duration: str
    duration_in_months: Optional[int] = None
    list_amount_micros: int
    discount_micros: int
    final_amount_micros: int
    online: bool


class PlanPriceOut(BaseModel):
    tier_key: str
    interval: str
    currency: str
    unit_amount: int
    unit_amount_micros: int
    price_id: Optional[str] = None
    entry_id: Optional[str] = None
    book_code: Optional[str] = None
    source: str


class TenantContractOut(BaseModel):
    contract: Optional[ContractOut] = None
