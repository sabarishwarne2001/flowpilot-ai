"""ARCH-32 — request and response shapes for the Redaction Studio.

WHAT IS DELIBERATELY ABSENT FROM EVERY RESPONSE MODEL
=====================================================

The matched text. There is no field for it, on any model here, and there is
no field that could carry it — no `excerpt`, no `preview`, no `label` that a
future convenience commit could fill with the value.

That is not squeamishness. `redaction_regions` stores an HMAC digest and
never the token, so the API physically cannot return the text; adding a
field for it would require adding a column for it, and the migration header
explains why that column does not exist. Keeping the absence visible HERE,
in the contract the frontend reads, is what stops somebody adding one.

The reviewer therefore works from: the detector's name, whether a checksum
validated it, where the box is, and how precisely it was placed. They can see
the value in the page preview like any other reader of the document.

WHY `token_digest` IS RETURNED AT ALL
=====================================

So the studio can group regions covering the same value — "this appears on
four pages" — without anyone reading it. It is a keyed MAC over a normalised
token; see `token_digest.py` for why a bare hash would not have been safe to
expose and why this one is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.redaction.vocabulary import (
    DEFAULT_RENDER_DPI,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    PROFILE_KEYS,
)


class RedactionStartRequest(BaseModel):
    profile_key: str = Field(..., description=f"One of {list(PROFILE_KEYS)}")
    render_dpi: int = Field(default=DEFAULT_RENDER_DPI, ge=MIN_RENDER_DPI, le=MAX_RENDER_DPI)
    restore_text_layer: bool = True


class RegionCreateRequest(BaseModel):
    """A rectangle the operator drew, in PDF points, origin bottom-left."""

    page_number: int = Field(..., ge=1)
    x0: float
    y0: float
    x1: float
    y1: float


class RegionToggleRequest(BaseModel):
    enabled: bool


class RegionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    detector: str
    confidence: float
    geometry_precision: str
    enabled: bool
    token_digest: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = None
    toggled_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime

    #: Derived, not stored. The studio renders it as a pill, and computing it
    #: here rather than in TypeScript keeps one definition of "which detectors
    #: are checksum-backed" — the vocabulary's.
    checksum_validated: bool = False


class LeakCheckResponse(BaseModel):
    passed: Optional[bool] = None
    #: The sentence §3.6 requires. Prose, not a JSON blob the reader decodes.
    sentence: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class RedactionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    work_item_id: uuid.UUID
    status: str
    profile_key: str
    render_dpi: int
    restore_text_layer: bool
    page_count: Optional[int] = None
    source_sha256: str
    output_sha256: Optional[str] = None
    input_digest: Optional[str] = None
    approved_by_user_id: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    regions: list[RegionResponse] = Field(default_factory=list)
    leak_check: LeakCheckResponse = Field(default_factory=LeakCheckResponse)

    #: §3.9. True when the profile includes a name detector, so the studio can
    #: state the limit on exactly the profiles it applies to. Computed from
    #: the vocabulary rather than hard-coded in the component.
    names_limit_applies: bool = False
    available_profiles: list[str] = Field(default_factory=lambda: list(PROFILE_KEYS))


class BundleResponse(BaseModel):
    document_url: Optional[str] = None
    manifest_url: Optional[str] = None
    output_sha256: Optional[str] = None
    expires_in: int = 900