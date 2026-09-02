"""ARCH-12 Step 6 — the click-to-cite contract (FE-B), and the moat."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CitationBoundingBox(BaseModel):
    """Axis-aligned rectangle, origin top-left."""

    x0: float
    y0: float
    x1: float
    y1: float

    width: Optional[int] = Field(
        None, description="Page width in the same space as the coordinates."
    )
    height: Optional[int] = Field(
        None, description="Page height in the same space as the coordinates."
    )
    space: Literal["pixels", "points", "normalized"] = Field(
        "pixels",
        description="Coordinate space.",
    )
    page: Optional[int] = Field(None, ge=1)

    @field_validator("x1")
    @classmethod
    def _x_ordered(cls, value: float, info) -> float:
        x0 = info.data.get("x0")
        if x0 is not None and value < x0:
            raise ValueError("x1 must be >= x0")
        return value

    @field_validator("y1")
    @classmethod
    def _y_ordered(cls, value: float, info) -> float:
        y0 = info.data.get("y0")
        if y0 is not None and value < y0:
            raise ValueError("y1 must be >= y0")
        return value

    @classmethod
    def from_chunk_bbox(cls, bbox: Optional[dict]) -> Optional["CitationBoundingBox"]:
        """Build from chunk bounding box as union_box writes it."""
        if not bbox:
            return None
        try:
            return cls(
                x0=float(bbox["x0"]),
                y0=float(bbox["y0"]),
                x1=float(bbox["x1"]),
                y1=float(bbox["y1"]),
                width=bbox.get("width"),
                height=bbox.get("height"),
                space=bbox.get("space", "pixels"),
                page=bbox.get("page"),
            )
        except (KeyError, TypeError, ValueError):
            return None


class CitationSource(BaseModel):
    """One retrieved passage that supports one claim."""

    work_item_id: uuid.UUID
    original_filename: str
    chunk_id: str = Field(
        ..., description="Stable public id: '{work_item_id}_chunk_{index}'."
    )
    chunk_index: int = Field(..., ge=0)
    page_number: Optional[int] = Field(None, ge=1)

    bbox: Optional[CitationBoundingBox] = None

    page_start_char: Optional[int] = Field(
        None, ge=0, description="Start offset of the chunk within the page text."
    )
    page_end_char: Optional[int] = Field(
        None, ge=0, description="End offset, exclusive."
    )

    snippet: str
    similarity_score: float = Field(0.0, ge=0.0, le=1.0)
    rank: int = Field(0, ge=0, description="Position after citation ranking.")

    @property
    def is_locatable(self) -> bool:
        """True when a viewer can highlight rather than merely navigate."""
        return self.bbox is not None and self.page_number is not None


class CitationClaim(BaseModel):
    """One span of the answer, and the evidence behind it."""

    claim_id: str
    text_span: tuple[int, int] = Field(
        ...,
        description="Character offsets into the post-redaction answer.",
    )
    sources: list[CitationSource] = Field(default_factory=list)


class CitationEnvelope(BaseModel):
    """The full provenance payload for one assistant message."""

    message_id: uuid.UUID
    conversation_id: uuid.UUID

    claims: list[CitationClaim] = Field(default_factory=list)

    context_hash: Optional[str] = Field(
        None,
        description="SHA-256 over the exact context string sent to the provider.",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    audit_log_id: Optional[uuid.UUID] = Field(
        None, description="ARCH-07 audit row sealing this generation."
    )

    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_version: Optional[str] = None
    generated_at: Optional[str] = None

    passages_included: int = 0
    passages_dropped_injection: int = 0
    passages_dropped_budget: int = 0

    truncated: bool = False
    finish_reason: Optional[str] = None
    usage_estimated: bool = False

    model_config = {"from_attributes": True}

    @property
    def is_sealed(self) -> bool:
        """A citation panel may only claim tamper-evidence when both exist."""
        return self.context_hash is not None and self.audit_log_id is not None


__all__ = [
    "CitationBoundingBox",
    "CitationClaim",
    "CitationEnvelope",
    "CitationSource",
]
