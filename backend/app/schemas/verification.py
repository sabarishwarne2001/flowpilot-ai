"""ARCH-13 Step 13.8 — verification API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.verification import DisagreementKind, VerificationStatus


class VerificationFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_path: str
    agreed: bool
    confidence: Decimal
    consensus_value: Optional[Any] = None
    agent_values: list[Any] = Field(default_factory=list)
    disagreement_kind: Optional[DisagreementKind] = None
    resolved_value: Optional[Any] = None


class VerificationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    work_item_id: uuid.UUID
    status: VerificationStatus
    agent_count: int
    agreement_score: Optional[Decimal] = None
    confidence: Optional[Decimal] = None
    cost_micros: int
    auto_approved: bool
    reviewed_by_user_id: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class VerificationDetailResponse(VerificationSummaryResponse):
    fields: list[VerificationFieldResponse] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationResolveRequest(BaseModel):
    """The reviewer's chosen value for each disagreed field."""

    values: dict[str, Any] = Field(
        ...,
        description=(
            "field_path -> chosen value. Must cover every disagreed field and "
            "no others."
        ),
    )

    @field_validator("values")
    @classmethod
    def non_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError(
                "values must not be empty. A resolve with no chosen values is "
                "an approval nobody made."
            )
        for key, chosen in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Invalid field path: {key!r}")
            if isinstance(chosen, (dict, list)):
                raise ValueError(
                    f"Field {key!r} was given a {type(chosen).__name__}. "
                    "Resolved values are scalars; a nested object is a "
                    "document fragment."
                )
        return value


__all__ = [
    "VerificationDetailResponse",
    "VerificationFieldResponse",
    "VerificationResolveRequest",
    "VerificationSummaryResponse",
]
