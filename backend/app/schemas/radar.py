"""ARCH-34 — request and response shapes for the Forensic Audit Radar API.

WHY `score` IS A STRING ON THE WIRE
===================================

`Decimal`, serialised as a string, not a float. `numeric(6,5)` does not round
and `0.97` does not exist in binary floating point, so a console that received
0.9699999999999999 and rendered it as a percentage would show 96% for a
finding the engine scored at 97% — and the threshold it is being compared
against is 0.97.

The same rule ARCH-33 applies to `threshold`, for the same reason.

WHY THERE IS NO `evidence` MODEL
================================

`evidence` is `list[dict[str, Any]]` and stays that way. Each layer produces a
different shape — identifiers, shingle samples, a chunk pair, a price series,
a clause pair — and the discriminator is `kind`, which the vocabulary owns. A
Pydantic union over five variants would have to be edited in lockstep with
every layer, and the failure mode of getting it wrong is a 500 on a finding
that is otherwise fine.

The console switches on `evidence[].kind` and renders what it knows; an
unknown kind renders as a labelled JSON block rather than disappearing.

WHY DISMISSAL'S REASON IS REQUIRED IN THE SCHEMA AND IN SQL
===========================================================

`ck_af_dismissal_has_reason` is the authority. This model refuses an empty
reason first so the customer gets a 422 naming the field rather than a 500
carrying a constraint name.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AnomalyFindingSummary(BaseModel):
    """One row of the feed. No evidence body — see `AnomalyFindingDetail`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    layer: str
    severity: str
    status: str
    score: Decimal
    headline: str
    subject_work_item_id: uuid.UUID
    counterpart_work_item_id: Optional[uuid.UUID] = None
    subject_label: str = ""
    counterpart_label: str = ""
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None


class AnomalyFindingDetail(AnomalyFindingSummary):
    """The feed row plus what a reviewer needs to check it."""

    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    engine_version: str = ""
    input_digest: str = ""


class AnomalyConfirmRequest(BaseModel):
    """Agreeing with the engine. A note is optional.

    Optional because agreement adds no information the finding does not
    already carry. Disagreement does, which is why dismissal's reason is
    mandatory.
    """

    note: Optional[str] = Field(default=None, max_length=2000)


class AnomalyDismissRequest(BaseModel):
    """Disagreeing with the engine. A reason is mandatory, and it is kept."""

    reason: str = Field(min_length=1, max_length=2000)
    #: None means never expires. Deliberately expressible — a supplier who
    #: resets their invoice series every April will collide every April — and
    #: deliberately not the default.
    ttl_days: Optional[int] = Field(default=365, ge=1, le=3650)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        body = (value or "").strip()
        if not body:
            raise ValueError(
                "A reason is required. 'Not an anomaly' with no reason is "
                "indistinguishable from a misclick six months later."
            )
        return body


class PriceSeriesPoint(BaseModel):
    work_item_id: uuid.UUID
    observed_on: date
    unit_price_micros: int


class PriceSeriesResponse(BaseModel):
    """What the chart draws: the points, the band, and the flagged one."""

    vendor_key: str
    sku: str
    currency: str
    points: list[PriceSeriesPoint] = Field(default_factory=list)
    median_micros: Optional[Decimal] = None
    mad_micros: Optional[Decimal] = None
    iqr_micros: Optional[Decimal] = None
    #: NULL when the series has no historical variation. The console renders
    #: "no historical variation to compare against" rather than a z of
    #: infinity, which is the whole MAD = 0 argument surfaced to the reader.
    z: Optional[Decimal] = None
    basis: Optional[str] = None
    flagged_work_item_id: Optional[uuid.UUID] = None


class AnomalySuppressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    layer: str
    item_a_id: uuid.UUID
    item_b_id: Optional[uuid.UUID] = None
    vendor_key: Optional[str] = None
    sku: Optional[str] = None
    reason: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class AnomalyFeedCounts(BaseModel):
    """The header line in §5.6's mock: "3 high · 7 medium · 12 dismissed"."""

    high: int = 0
    medium: int = 0
    low: int = 0
    dismissed: int = 0
    confirmed: int = 0
    open: int = 0


class AnomalyFeedResponse(BaseModel):
    counts: AnomalyFeedCounts
    items: list[AnomalyFindingSummary] = Field(default_factory=list)


__all__ = [
    "AnomalyFindingSummary",
    "AnomalyFindingDetail",
    "AnomalyConfirmRequest",
    "AnomalyDismissRequest",
    "PriceSeriesPoint",
    "PriceSeriesResponse",
    "AnomalySuppressionResponse",
    "AnomalyFeedCounts",
    "AnomalyFeedResponse",
]
