"""ARCH-31 Step 3 — DTOs for the procurement matching API.

WHY THE APPROVE REQUEST DOES NOT VALIDATE THE OVERRIDE REASON
=============================================================

`CaseApproveRequest.override_reason` is optional here, and the 422 for a
missing reason is raised by `case_service.approve_case`. That looks backwards
— Pydantic is where required fields belong — and it is deliberate.

Whether a reason is REQUIRED depends on whether the case has red lines, which
is a fact about database state, not about the request body. A validator that
knew it would have to load the case, which puts a query inside a schema. So
the schema accepts the field and the service, which has the case in hand,
decides. `verify_arch31.py` asserts the refusal comes back as the ARCH-01
envelope with the right code rather than as a Pydantic error, because the two
shapes are different and the console branches on one of them.

The dispute reason IS validated here: ten characters is a fact about the
string, and nothing else needs loading to know it.

RESPONSE MODELS DO NOT INHERIT FROM REQUEST MODELS
==================================================

Same rule ARCH-26 wrote down for warehouse credentials, applied for a
different reason. Nothing here is secret, but a case response carries
`policy_version` and `input_digest`, which are stamps a client must never be
able to set. Sharing a base class is how a writable field appears on the
request side eighteen months from now without anyone noticing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.procurement_matching.case_service import MINIMUM_DISPUTE_REASON
from app.services.procurement_matching.vocabulary import (
    CASE_STATUSES,
    LINE_OUTCOMES,
)

__all__ = [
    "CaseLineResponse",
    "CaseSummaryResponse",
    "CaseDetailResponse",
    "CaseApproveRequest",
    "CaseDisputeRequest",
    "CaseRematchRequest",
    "TolerancePolicyResponse",
    "TolerancePolicyPublishRequest",
    "ImpactPreviewRequest",
    "ImpactPreviewResponse",
    "DocumentRoleOverrideRequest",
]


class CaseLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_number: int
    outcome: str
    description: Optional[str] = None
    sku: Optional[str] = None

    po_line_index: Optional[int] = None
    po_quantity: Optional[Decimal] = None
    po_unit_price_micros: Optional[int] = None
    po_amount_micros: Optional[int] = None

    receipt_line_index: Optional[int] = None
    receipt_quantity: Optional[Decimal] = None

    invoice_line_index: Optional[int] = None
    invoice_quantity: Optional[Decimal] = None
    invoice_unit_price_micros: Optional[int] = None
    invoice_amount_micros: Optional[int] = None

    pair_cost: Optional[int] = None
    price_delta_micros: Optional[int] = None
    quantity_delta: Optional[Decimal] = None

    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CaseSummaryResponse(BaseModel):
    """The queue row. Deliberately without lines — a queue of 200 cases that
    each carry their lines is a payload nobody reads and a query nobody
    planned for."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    vendor_key: Optional[str] = None
    po_work_item_id: Optional[uuid.UUID] = None
    receipt_work_item_id: Optional[uuid.UUID] = None
    invoice_work_item_id: Optional[uuid.UUID] = None
    line_count: int
    exception_count: int
    variance_micros: int
    policy_version: str
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None


class CaseDetailResponse(CaseSummaryResponse):
    input_digest: str
    header_findings: list[dict[str, Any]] = Field(default_factory=list)
    resolution_reason: Optional[str] = None
    resolved_by_user_id: Optional[uuid.UUID] = None
    lines: list[CaseLineResponse] = Field(default_factory=list)


class CaseApproveRequest(BaseModel):
    # Optional here on purpose. See the module header.
    override_reason: Optional[str] = Field(default=None, max_length=2000)


class CaseDisputeRequest(BaseModel):
    reason: str = Field(min_length=MINIMUM_DISPUTE_REASON, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        text = value.strip()
        if len(text) < MINIMUM_DISPUTE_REASON:
            raise ValueError(
                f"a dispute reason must be at least {MINIMUM_DISPUTE_REASON} "
                f"characters of actual text; this is what goes to the supplier"
            )
        return text


class CaseRematchRequest(BaseModel):
    """Force specific counterparts. Both optional: naming only the PO is a
    legitimate correction when the receipt was found correctly."""

    po_work_item_id: Optional[uuid.UUID] = None
    receipt_work_item_id: Optional[uuid.UUID] = None


class TolerancePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    price_tolerance_micros: int
    price_tolerance_bps: int
    quantity_tolerance: Decimal
    max_pair_cost: int
    candidate_window_days: int
    published_at: Optional[datetime] = None
    created_at: datetime


class TolerancePolicyPublishRequest(BaseModel):
    price_tolerance_micros: int = Field(default=0, ge=0)
    price_tolerance_bps: int = Field(default=0, ge=0, le=10_000)
    quantity_tolerance: Decimal = Field(default=Decimal(0), ge=0)
    # Bounded by ck_procurement_tolerance_policies_pair_cost_range. A value
    # at or above the scale ceiling rejects nothing, which is the same as
    # having no threshold — the defect the column exists to prevent.
    max_pair_cost: int = Field(default=600_000, gt=0, lt=1_000_000)
    candidate_window_days: int = Field(default=90, ge=0, le=730)


class ImpactPreviewRequest(TolerancePolicyPublishRequest):
    window_days: int = Field(default=30, ge=1, le=365)


class ImpactPreviewResponse(BaseModel):
    window_days: int
    cases_considered: int
    exceptions_today: int
    exceptions_under_draft: int
    lines_that_would_clear: int
    lines_that_would_flag: int
    caveat: str


class DocumentRoleOverrideRequest(BaseModel):
    """A person correcting the classifier. `role_source` is not a field:
    the route writes 'USER' unconditionally, because a client that could
    send 'CLASSIFIER' could launder a machine guess into a human decision
    and the USER lock would stop meaning anything."""

    role: str

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        from app.services.procurement_matching.role_classifier import ROLES

        candidate = value.strip().upper()
        if candidate not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        return candidate