"""ARCH-18 — COGS, margin and supplier reconciliation DTOs.

Every optional numeric field here is optional on purpose. `gross_margin_micros`
is None when no cost is known; `unknown_cost_share` is None when there is no
revenue to take a share of; `variance_ratio` is None when the modelled total is
zero. The frontend renders each of those as "unknown" and never as a number,
which is the whole reason they are not defaulted to 0 for the convenience of
the serializer.

Micros are serialised as JSON numbers, not strings. They are integers bounded
by BIGINT, and JavaScript holds integers exactly to 2^53 — about 9 billion
dollars expressed in micros. Ratios are floats because they are already
approximations and pretending otherwise in the wire format would be theatre.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------


class MarginFiguresResponse(BaseModel):
    """The numeric core shared by the platform summary and every tenant row."""

    revenue_micros: int
    attributed_revenue_micros: int = Field(
        description=(
            "Revenue on rows that also carry a cost basis. The only revenue a "
            "gross margin is computed against."
        )
    )
    cost_basis_micros: int
    unknown_cost_revenue_micros: int

    gross_margin_micros: Optional[int] = Field(
        default=None,
        description="None when no row in the window has a known cost.",
    )
    gross_margin_ratio: Optional[float] = None

    unknown_cost_share: Optional[float] = Field(
        default=None,
        description=(
            "Share of revenue excluded from the margin, by value not by row "
            "count. Read this before reading the margin."
        ),
    )
    soft_cost_share: Optional[float] = Field(
        default=None,
        description="Share of attributed revenue whose cost is ESTIMATED.",
    )

    event_count: int
    known_cost_event_count: int
    unknown_cost_event_count: int

    is_trustworthy: bool = Field(
        description=(
            "False when too little of the revenue has a known cost for the "
            "margin to be quotable. The dashboard suppresses the headline "
            "figure rather than showing a confident wrong number."
        )
    )

    model_config = ConfigDict(from_attributes=True)


class PlatformMarginSummaryResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    currency: str
    organization_count: int
    figures: MarginFiguresResponse

    model_config = ConfigDict(from_attributes=True)


class TenantEconomicsEntry(BaseModel):
    organization_id: uuid.UUID
    organization_name: Optional[str] = None
    organization_slug: Optional[str] = None
    figures: MarginFiguresResponse

    model_config = ConfigDict(from_attributes=True)


class TenantEconomicsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    currency: str
    order: str
    entries: list[TenantEconomicsEntry] = []

    model_config = ConfigDict(from_attributes=True)


class ProviderCostEntry(BaseModel):
    provider: Optional[str] = None
    cost_basis_micros: int
    revenue_micros: int
    event_count: int
    unknown_cost_event_count: int

    model_config = ConfigDict(from_attributes=True)


class ProviderCostResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    entries: list[ProviderCostEntry] = []

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------


class RateCardEntry(BaseModel):
    event_type: str
    provider: str
    model: Optional[str] = None
    tier_key: Optional[str] = None
    unit: str
    unit_price_micros: Decimal
    cost_basis_micros: Optional[Decimal] = None
    cost_basis_source: Optional[str] = None
    unit_margin_micros: Optional[Decimal] = Field(
        default=None,
        description="price - cost. None when the cost is unknown.",
    )
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    @field_serializer("unit_price_micros", "cost_basis_micros", "unit_margin_micros")
    def _decimal_as_string(self, value: Optional[Decimal]) -> Optional[str]:
        # Nine decimal places of per-unit price does not survive a float. The
        # frontend treats these as display strings and never does arithmetic
        # on them; totals come from the integer micros fields above.
        return None if value is None else format(value, "f")


class RateCardResponse(BaseModel):
    price_book_id: Optional[uuid.UUID] = None
    price_book_version: Optional[int] = None
    currency: Optional[str] = None
    effective_from: Optional[datetime] = None
    entry_count: int = 0
    with_cost_basis: int = 0
    hard_cost_basis: int = 0
    coverage_ratio: Optional[float] = None
    entries: list[RateCardEntry] = []

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Supplier invoices and reconciliation
# ---------------------------------------------------------------------------


class SupplierInvoiceCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    period_start: date
    period_end: date = Field(
        description="Last day covered, INCLUSIVE. '1 Jul - 31 Jul' is 31 Jul."
    )
    invoiced_total_micros: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    invoice_reference: Optional[str] = Field(default=None, max_length=200)
    raw_document_file_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "A platform-scoped upload only. A tenant's file is refused: the "
            "FK is RESTRICT and would pin that tenant's document against "
            "their own deletion."
        ),
    )
    notes: Optional[str] = Field(default=None, max_length=1000)

    model_config = ConfigDict(protected_namespaces=())


class SupplierReconciliationResponse(BaseModel):
    id: uuid.UUID
    supplier_invoice_id: uuid.UUID
    modelled_total_micros: int
    variance_micros: int
    variance_ratio: Optional[float] = None
    status: str
    modelled_event_count: int
    unknown_cost_event_count: int
    note: Optional[str] = None
    reconciled_at: datetime
    reconciled_by_user_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("variance_ratio")
    def _ratio_as_float(self, value: Optional[float]) -> Optional[float]:
        return None if value is None else float(value)


class SupplierInvoiceResponse(BaseModel):
    id: uuid.UUID
    provider: str
    invoice_reference: Optional[str] = None
    period_start: date
    period_end: date
    invoiced_total_micros: int
    currency: str
    raw_document_file_id: Optional[uuid.UUID] = None
    ingested_at: datetime
    ingested_by_user_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    latest_reconciliation: Optional[SupplierReconciliationResponse] = None

    model_config = ConfigDict(from_attributes=True)


class SupplierInvoiceListResponse(BaseModel):
    entries: list[SupplierInvoiceResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ReconcileRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=4000)
    threshold_ratio: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Override the configured variance threshold for this run.",
    )
    force: bool = Field(
        default=False,
        description=(
            "Reconcile a period that has not been closed long enough to be "
            "final. Recorded on the resulting row."
        ),
    )


class AcceptVarianceRequest(BaseModel):
    note: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "Mandatory. An accepted variance with no stated reason is "
            "indistinguishable from a mistake."
        ),
    )


MarginOrder = Literal["MARGIN_ASC", "MARGIN_DESC", "REVENUE_DESC", "UNKNOWN_DESC"]


__all__ = [
    "AcceptVarianceRequest",
    "MarginFiguresResponse",
    "MarginOrder",
    "PlatformMarginSummaryResponse",
    "ProviderCostEntry",
    "ProviderCostResponse",
    "RateCardEntry",
    "RateCardResponse",
    "ReconcileRequest",
    "SupplierInvoiceCreate",
    "SupplierInvoiceListResponse",
    "SupplierInvoiceResponse",
    "SupplierReconciliationResponse",
    "TenantEconomicsEntry",
    "TenantEconomicsResponse",
]