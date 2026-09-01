"""ARCH-20 — data governance, residency and compliance DTOs.

Two things in here are deliberately not what a first draft would produce.

`ComplianceExportResponse` has no `download_url` field. The URL is minted per
request from `storage_key` and served by its own endpoint, because a presigned
URL is a bearer credential with a TTL and does not belong in a list payload
that a browser will cache.

`RetentionPolicyUpdate.audit_retention_days` is floored at 400 in the schema
as well as in the database. Rejecting it at the boundary produces a 422 that
names the reason; letting it reach PostgreSQL produces an IntegrityError that
names a constraint. The first is an explanation, the second is a stack trace.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.compliance import AUDIT_RETENTION_FLOOR_DAYS, MINIMUM_RETENTION_DAYS

DataResidencyRegion = Literal["US", "EU", "APAC", "GLOBAL"]

ComplianceExportStatus = Literal[
    "PENDING", "RUNNING", "COMPLETE", "FAILED", "EXPIRED"
]


# ---------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------


class ResidencyRegionOption(BaseModel):
    """One selectable region and whether this deployment can actually serve it."""

    region: DataResidencyRegion
    configured: bool = Field(
        description=(
            "True when a bucket is provisioned for this region. A region that "
            "is not configured is offered read-only: selecting it would pin a "
            "tenant to storage that does not exist."
        )
    )


class DataResidencyResponse(BaseModel):
    region: DataResidencyRegion
    available_regions: list[ResidencyRegionOption]


class DataResidencyUpdate(BaseModel):
    region: DataResidencyRegion
    acknowledge_no_migration: bool = Field(
        default=False,
        description=(
            "Must be true. Repinning is forward-looking: objects already "
            "written stay in the bucket they were written to. The flag exists "
            "so that acknowledgement is recorded rather than assumed."
        ),
    )

    @field_validator("acknowledge_no_migration")
    @classmethod
    def _must_acknowledge(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "Repinning does not move existing objects. Set "
                "acknowledge_no_migration=true to confirm you understand that "
                "data written under the previous region stays where it is."
            )
        return value


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


class RetentionPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID
    work_item_retention_days: Optional[int] = None
    audit_retention_days: Optional[int] = None
    conversation_retention_days: Optional[int] = None
    auto_purge_enabled: bool = False
    audit_retention_floor_days: int = AUDIT_RETENTION_FLOOR_DAYS
    updated_at: Optional[datetime] = None


class RetentionPolicyUpdate(BaseModel):
    work_item_retention_days: Optional[int] = Field(
        default=None, ge=MINIMUM_RETENTION_DAYS
    )
    audit_retention_days: Optional[int] = Field(
        default=None,
        ge=AUDIT_RETENTION_FLOOR_DAYS,
        description=(
            f"Floored at {AUDIT_RETENTION_FLOOR_DAYS}. ARCH-07's audit "
            f"immutability trigger refuses DELETE below that age regardless "
            f"of what this value says, so a lower number would be a policy "
            f"the database declines to honour."
        ),
    )
    conversation_retention_days: Optional[int] = Field(
        default=None, ge=MINIMUM_RETENTION_DAYS
    )
    auto_purge_enabled: bool = Field(
        default=False,
        description=(
            "There is no soft-delete on work_items or conversations, so a "
            "purge destroys live rows. Off unless explicitly chosen."
        ),
    )


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


class ErasureRequest(BaseModel):
    subject_user_id: uuid.UUID
    erasure_ticket: str = Field(
        min_length=1,
        max_length=120,
        description="Your DSAR or case reference. Recorded verbatim as evidence.",
    )
    confirm_subject_email: str = Field(
        min_length=3,
        max_length=255,
        description=(
            "The subject's current address, retyped. Checked against the row "
            "before anything is destroyed. Erasure is irreversible and a "
            "mistyped user id is otherwise indistinguishable from a correct one."
        ),
    )

    @field_validator("erasure_ticket")
    @classmethod
    def _ticket_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("An erasure ticket reference is required.")
        return cleaned


class ErasurePreviewResponse(BaseModel):
    subject_user_id: uuid.UUID
    counts: dict[str, int]
    preserved_tables: list[str]


class ErasedSubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    subject_user_id: Optional[uuid.UUID] = None
    subject_email_hash: str
    erasure_ticket: str
    erased_by_user_id: Optional[uuid.UUID] = None
    erased_at: datetime
    details: Optional[dict[str, Any]] = None


class ErasureResultResponse(BaseModel):
    erased_subject: ErasedSubjectResponse
    already_erased: bool = Field(
        description=(
            "True when a tombstone for this subject already existed. The "
            "request succeeded and nothing was destroyed a second time."
        )
    )
    counts: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class ComplianceExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    requested_by_user_id: Optional[uuid.UUID] = None
    status: ComplianceExportStatus
    residency_region: DataResidencyRegion
    file_size_bytes: Optional[int] = None
    error_message: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class ComplianceExportDownloadResponse(BaseModel):
    export_id: uuid.UUID
    download_url: str
    expires_in_seconds: int


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class ComplianceOverviewResponse(BaseModel):
    organization_id: uuid.UUID
    residency: DataResidencyResponse
    retention: RetentionPolicyResponse
    erasure_count: int
    export_count: int
    latest_export: Optional[ComplianceExportResponse] = None