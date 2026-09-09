"""ARCH-21 §4.4 — the public gateway's request and response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RateLimitSnapshot(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
    code: str
    message: str


class PublicApiVersion(BaseModel):
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
