"""ARCH-40 — the review hub's wire shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.review import vocabulary as vocab
from app.services.review.resolution import ANOMALY_VERDICTS


class ReviewItemResponse(BaseModel):
    """The stable projection. One shape for all three sources."""

    kind: str
    item_id: uuid.UUID
    work_item_id: Optional[uuid.UUID] = None
    document_name: Optional[str] = None
    headline: str
    severity: str
    confidence: Optional[float] = None
    created_at: datetime
    age_seconds: int
    status: str
    assignee_user_id: Optional[uuid.UUID] = None
    assignee_email: Optional[str] = None
    #: A reviewer resolving an item on a held document needs to know the hold
    #: exists: the document cannot be deleted or exported while it stands.
    under_retention_hold: bool = False
    tags: list[str] = Field(default_factory=list)
    #: ARCH40-S1:review-reason-wire. DISAGREEMENT, ESCALATION,
    #: CALIBRATION_HOLD, AUTONOMY_AUDIT, PENDING_REVIEW, CLAUSE_TRIAGE, ANOMALY.
    review_reason: str = ""

    model_config = ConfigDict(from_attributes=True)


class ReviewQueueResponse(BaseModel):
    items: list[ReviewItemResponse]
    total: int
    page: int
    page_size: int
    #: Per-kind counts for the queue header, computed over the same filtered
    #: set as `total` — not over the whole workspace, which would make the
    #: header disagree with the list the moment a filter was applied.
    counts_by_kind: dict[str, int]
    #: ARCH40-S1:hub-allowed-kinds. What this organization's plan lets it see.
    #: The console draws tabs from this, so a gated kind is absent rather than
    #: shown empty.
    allowed_kinds: list[str] = Field(default_factory=list)


class ReviewResolveRequest(BaseModel):
    """The union of what the three sources need, validated per kind server-side."""

    values: Optional[dict[str, Any]] = None
    reviewer_verdict: Optional[str] = None
    corrected_quote: Optional[str] = None
    corrected_value: Optional[dict[str, Any]] = None
    anomaly_verdict: Optional[str] = None
    note: Optional[str] = None
    ttl_days: Optional[int] = Field(default=None, ge=1, le=3650)
    #: ARCH42-S1:merge-verdict. MERGE reviews: MERGE or SEPARATE.
    merge_verdict: Optional[str] = None

    @field_validator("merge_verdict")
    @classmethod
    def _known_merge_verdict(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in ("MERGE", "SEPARATE"):
            raise ValueError("merge_verdict must be one of: MERGE, SEPARATE")
        return upper

    #: ARCH43-S1:split-verdict. SPLIT reviews: APPROVE (optionally with the
    #: corrected first page of every document after the first) or REJECT.
    split_verdict: Optional[str] = None
    split_boundaries: Optional[list[int]] = None
    #: ARCH44-S1:table-verdict. TABLE reviews: ACCEPT or REJECT.
    table_verdict: Optional[str] = None

    @field_validator("table_verdict")
    @classmethod
    def _known_table_verdict(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in ("ACCEPT", "REJECT"):
            raise ValueError("table_verdict must be one of: ACCEPT, REJECT")
        return upper

    @field_validator("split_verdict")
    @classmethod
    def _known_split_verdict(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in ("APPROVE", "REJECT"):
            raise ValueError("split_verdict must be one of: APPROVE, REJECT")
        return upper

    @field_validator("anomaly_verdict")
    @classmethod
    def _known_anomaly_verdict(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in ANOMALY_VERDICTS:
            raise ValueError(
                f"anomaly_verdict must be one of: {', '.join(ANOMALY_VERDICTS)}"
            )
        return upper


class ReviewResolveResponse(BaseModel):
    kind: str
    item_id: uuid.UUID
    work_item_id: Optional[uuid.UUID] = None
    resolution: str


class ReviewAssignRequest(BaseModel):
    assignee_user_id: uuid.UUID


class ReviewBulkRequest(BaseModel):
    """ARCH-38's bulk shape, reused verbatim.

    `{action, ids[], idempotency_key}` is what `api/v1/ingestion.py` accepts
    and what the console's bulk bar already sends. `kind` is a sibling field
    rather than a prefix baked into each id, because an id list of
    `"ANOMALY:uuid"` strings would be a second encoding of a discriminator the
    request already carries — and the per-item result contract would then have
    to parse it back out.
    """

    action: str
    kind: str
    ids: list[uuid.UUID] = Field(min_length=1, max_length=vocab.MAX_BULK_IDS)
    idempotency_key: str = Field(min_length=8, max_length=128)
    assignee_user_id: Optional[uuid.UUID] = None
    payload: Optional[ReviewResolveRequest] = None

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        if value not in vocab.BULK_ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(vocab.BULK_ACTIONS)}")
        return value

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in vocab.KINDS:
            raise ValueError(f"kind must be one of: {', '.join(vocab.KINDS)}")
        return value


class ReviewBulkItemResult(BaseModel):
    """ARCH-38's `bulk_service.ItemResult`, field for field.

    `work_item_id` keeps its name even though the hub's unit is a review item:
    the console's bulk result renderer reads that key, and a second contract
    that differs in one field name is the drift ARCH-38 wrote ItemResult to
    prevent. The review item id travels in `review_item_id` alongside it.
    """

    work_item_id: str
    outcome: str
    code: Optional[str] = None
    detail: Optional[str] = None
    review_item_id: Optional[str] = None


class ReviewBulkResponse(BaseModel):
    action: str
    kind: str
    results: list[ReviewBulkItemResult]
    ok: int
    refused: int
    skipped: int


class ReviewAssigneeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    open_items: int


__all__ = [
    "ReviewAssignRequest",
    "ReviewAssigneeResponse",
    "ReviewBulkItemResult",
    "ReviewBulkRequest",
    "ReviewBulkResponse",
    "ReviewItemResponse",
    "ReviewQueueResponse",
    "ReviewResolveRequest",
    "ReviewResolveResponse",
]
