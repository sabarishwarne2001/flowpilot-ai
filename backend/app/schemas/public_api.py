"""ARCH-21 §4.4 — the public gateway's request and response contracts.

These are a PUBLIC contract in the versioning sense: external code depends on
the field names, and removing or renaming one is a breaking change that needs
the `Sunset` / `Deprecation` header machinery in `app/api/v1/public/gateway.py`,
not a patch release. Additions are safe; subtractions are not.

Every response carries `RateLimitSnapshot`. Putting the budget in the body as
well as the headers is redundant on purpose — the headers are the standard and
the SDKs read them, but a developer debugging through a proxy that strips
non-safelisted headers has otherwise no way to see why they were throttled.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RateLimitSnapshot(BaseModel):
    """The caller's remaining budget, mirrored from the response headers."""

    tier: str = Field(description="Rate tier that produced these numbers.")
    limit: int = Field(description="Requests permitted per minute.")
    remaining: int = Field(description="Requests left in the current window.")
    reset_seconds: int = Field(
        description="Seconds until the current window resets."
    )


class PublicDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    filename: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: Optional[str] = None
    pipeline_stage: Optional[str] = None
    page_count: Optional[int] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PublicDocumentPage(BaseModel):
    items: list[PublicDocument]
    total: int
    page: int
    page_size: int
    rate_limit: RateLimitSnapshot


class PublicDocumentResponse(BaseModel):
    document: PublicDocument
    rate_limit: RateLimitSnapshot


class PublicQueryRequest(BaseModel):
    workspace_id: uuid.UUID = Field(
        description=(
            "Workspace to search. Must belong to the organization that owns "
            "the API key; a workspace in another tenant returns 404."
        )
    )
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=50)
    work_item_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "Restrict retrieval to these documents. An empty list is not the "
            "same as omitting the field: it matches nothing."
        ),
    )


class PublicQueryResult(BaseModel):
    id: str
    text: str
    document_name: str
    work_item_id: str
    chunk_index: Optional[int] = None
    page_number: Optional[int] = None
    similarity_score: Optional[float] = None


class PublicQueryResponse(BaseModel):
    results: list[PublicQueryResult]
    result_count: int
    latency_ms: float
    tier: str
    #: The HNSW candidate-list depth this tier bought, reported rather than
    #: assumed. A caller comparing recall between tiers needs to see the knob
    #: that changed, and an operator debugging a slow query needs to know
    #: whether the tuning actually applied.
    ef_search: int
    retrieval_arms: list[str] = Field(default_factory=list)
    rate_limit: RateLimitSnapshot


class PublicWorkflow(BaseModel):
    id: str
    workspace_id: str
    name: str
    event: str
    priority: int
    is_active: bool
    graph_version: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PublicWorkflowList(BaseModel):
    items: list[PublicWorkflow]
    rate_limit: RateLimitSnapshot


class PublicWorkflowTriggerRequest(BaseModel):
    workspace_id: uuid.UUID
    work_item_id: uuid.UUID


class PublicWorkflowTriggerResponse(BaseModel):
    outbox_event_id: str
    rule_id: str
    work_item_id: str
    status: str = Field(
        description=(
            "Always QUEUED. The gateway raises an event; the automation "
            "engine decides which rules match it."
        )
    )
    note: str
    rate_limit: RateLimitSnapshot


class PublicErrorBody(BaseModel):
    """The gateway's error envelope.

    Flat and stable. FastAPI's default `{"detail": ...}` shape is preserved
    as the transport, and this model documents what external code should
    parse — `code` is the machine-readable half and does not change when the
    prose in `message` is improved.
    """

    code: str
    message: str


class PublicApiVersion(BaseModel):
    """Served by `GET /api/v1/public` so a client can assert compatibility."""

    version: str
    status: str = Field(description="STABLE | DEPRECATED | SUNSET")
    deprecation: Optional[datetime] = None
    sunset: Optional[datetime] = None
    documentation_url: Optional[str] = None
    supported_scopes: list[str] = Field(default_factory=list)


__all__ = [
    "PublicApiVersion",
    "PublicDocument",
    "PublicDocumentPage",
    "PublicDocumentResponse",
    "PublicErrorBody",
    "PublicQueryRequest",
    "PublicQueryResponse",
    "PublicQueryResult",
    "PublicWorkflow",
    "PublicWorkflowList",
    "PublicWorkflowTriggerRequest",
    "PublicWorkflowTriggerResponse",
    "RateLimitSnapshot",
]
