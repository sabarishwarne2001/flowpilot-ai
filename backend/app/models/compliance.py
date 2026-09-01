"""ARCH-20 — data governance, residency and subject erasure."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

DATA_RESIDENCY_REGION_VALUES: tuple[str, ...] = ("US", "EU", "APAC", "GLOBAL")

REGION_US: str = "US"
REGION_EU: str = "EU"
REGION_APAC: str = "APAC"
REGION_GLOBAL: str = "GLOBAL"

PINNED_REGIONS: frozenset[str] = frozenset({REGION_US, REGION_EU, REGION_APAC})

COMPLIANCE_EXPORT_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "EXPIRED",
)

EXPORT_PENDING: str = "PENDING"
EXPORT_RUNNING: str = "RUNNING"
EXPORT_COMPLETE: str = "COMPLETE"
EXPORT_FAILED: str = "FAILED"
EXPORT_EXPIRED: str = "EXPIRED"

TERMINAL_EXPORT_STATUSES: frozenset[str] = frozenset(
    {EXPORT_COMPLETE, EXPORT_FAILED, EXPORT_EXPIRED}
)

AUDIT_RETENTION_FLOOR_DAYS: int = 400
MINIMUM_RETENTION_DAYS: int = 30
ERASED_EMAIL_DOMAIN: str = "erased.invalid"

_REGION_IN = ", ".join(f"'{v}'" for v in DATA_RESIDENCY_REGION_VALUES)
_STATUS_IN = ", ".join(f"'{v}'" for v in COMPLIANCE_EXPORT_STATUS_VALUES)


def erased_email_for(user_id: uuid.UUID | str) -> str:
    return f"erased+{user_id}@{ERASED_EMAIL_DOMAIN}"


class ErasedSubject(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "erased_subjects"

    __table_args__ = (
        CheckConstraint(
            "length(subject_email_hash) = 64",
            name="email_hash_is_sha256",
        ),
        CheckConstraint("length(erasure_ticket) > 0", name="ticket_not_blank"),
        Index(
            "uq_erased_subjects_org_email_hash",
            "organization_id",
            "subject_email_hash",
            unique=True,
        ),
        Index(
            "ix_erased_subjects_organization_id_erased_at",
            "organization_id",
            text("erased_at DESC"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            name="fk_erased_subjects_organization_id_organizations",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    subject_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_erased_subjects_subject_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    subject_email_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    erasure_ticket: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    erased_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_erased_subjects_erased_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ErasedSubject org={self.organization_id} "
            f"subject={self.subject_user_id} ticket={self.erasure_ticket!r}>"
        )


class ComplianceExport(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "compliance_exports"

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_IN})", name="status_vocabulary"),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="size_non_negative",
        ),
        CheckConstraint(
            "status <> 'COMPLETE' OR storage_key IS NOT NULL",
            name="complete_has_key",
        ),
        CheckConstraint(
            f"residency_region IN ({_REGION_IN})",
            name="region_vocabulary",
        ),
        Index(
            "ix_compliance_exports_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            name="fk_compliance_exports_organization_id_organizations",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    requested_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_compliance_exports_requested_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'PENDING'"),
        default=EXPORT_PENDING,
    )
    storage_key: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )
    residency_region: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'GLOBAL'"),
        default=REGION_GLOBAL,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def is_downloadable(self) -> bool:
        return self.status == EXPORT_COMPLETE and bool(self.storage_key)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ComplianceExport org={self.organization_id} "
            f"status={self.status} region={self.residency_region}>"
        )


class RetentionPolicy(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "retention_policies"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            name="uq_retention_policies_organization_id",
        ),
        CheckConstraint(
            f"audit_retention_days IS NULL "
            f"OR audit_retention_days >= {AUDIT_RETENTION_FLOOR_DAYS}",
            name="audit_floor",
        ),
        CheckConstraint(
            f"work_item_retention_days IS NULL "
            f"OR work_item_retention_days >= {MINIMUM_RETENTION_DAYS}",
            name="work_item_minimum",
        ),
        CheckConstraint(
            f"conversation_retention_days IS NULL "
            f"OR conversation_retention_days >= {MINIMUM_RETENTION_DAYS}",
            name="conversation_minimum",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            name="fk_retention_policies_organization_id_organizations",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    work_item_retention_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    audit_retention_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    conversation_retention_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    auto_purge_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RetentionPolicy org={self.organization_id} "
            f"auto_purge={self.auto_purge_enabled}>"
        )