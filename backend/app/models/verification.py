"""ARCH-13 Step 13.7/13.8 — multi-agent verification records."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.work_item import WorkItem


class VerificationStatus(str, PyEnum):
    PENDING = "PENDING"
    AGREED = "AGREED"
    DISAGREED = "DISAGREED"
    REVIEWED = "REVIEWED"
    AUTO_APPROVED = "AUTO_APPROVED"


class DisagreementKind(str, PyEnum):
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    FORMAT = "FORMAT"


BLOCKING_STATUSES: tuple[VerificationStatus, ...] = (
    VerificationStatus.PENDING,
    VerificationStatus.DISAGREED,
)

RELEASING_STATUSES: tuple[VerificationStatus, ...] = (
    VerificationStatus.AGREED,
    VerificationStatus.AUTO_APPROVED,
    VerificationStatus.REVIEWED,
)

TERMINAL_VERIFICATION_STATUSES: tuple[VerificationStatus, ...] = (
    VerificationStatus.AGREED,
    VerificationStatus.DISAGREED,
    VerificationStatus.REVIEWED,
    VerificationStatus.AUTO_APPROVED,
)

VERIFICATION_STATUS_ENUM_NAME = "document_verification_status"
DISAGREEMENT_KIND_ENUM_NAME = "document_disagreement_kind"

_TERMINAL_SQL = ", ".join(f"'{s.value}'" for s in TERMINAL_VERIFICATION_STATUSES)


class DocumentVerification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "document_verifications"

    __table_args__ = (
        CheckConstraint(
            "agent_count >= 2 AND agent_count <= 5",
            name="ck_document_verifications_agent_count_bounded",
        ),
        CheckConstraint(
            "agreement_score IS NULL OR "
            "(agreement_score >= 0 AND agreement_score <= 1)",
            name="ck_document_verifications_agreement_score_ratio",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_document_verifications_confidence_ratio",
        ),
        CheckConstraint(
            "cost_micros >= 0", name="ck_document_verifications_cost_non_negative"
        ),
        CheckConstraint(
            f"(status = 'REVIEWED'::{VERIFICATION_STATUS_ENUM_NAME}) = "
            "(reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_document_verifications_reviewer_matches_status",
        ),
        CheckConstraint(
            "NOT (auto_approved AND reviewed_by_user_id IS NOT NULL)",
            name="ck_document_verifications_auto_approved_has_no_reviewer",
        ),
        CheckConstraint(
            f"(status IN ({_TERMINAL_SQL})) = (agreement_score IS NOT NULL)",
            name="ck_document_verifications_score_matches_status",
        ),
        Index(
            "uq_document_verifications_open_work_item",
            "work_item_id",
            unique=True,
            postgresql_where=text(
                f"status IN ('PENDING'::{VERIFICATION_STATUS_ENUM_NAME}, "
                f"'DISAGREED'::{VERIFICATION_STATUS_ENUM_NAME})"
            ),
        ),
        Index(
            "ix_document_verifications_workspace_status",
            "workspace_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_document_verifications_pending",
            "created_at",
            postgresql_where=text(
                f"status = 'PENDING'::{VERIFICATION_STATUS_ENUM_NAME}"
            ),
        ),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[VerificationStatus] = mapped_column(
        PGEnum(
            VerificationStatus,
            name=VERIFICATION_STATUS_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )

    agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    agreement_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    auto_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    fields: Mapped[list["DocumentVerificationField"]] = relationship(
        "DocumentVerificationField",
        back_populates="verification",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentVerificationField.field_path",
    )
    work_item: Mapped["WorkItem"] = relationship("WorkItem")

    @property
    def blocks_automation(self) -> bool:
        return self.status in BLOCKING_STATUSES

    @property
    def disagreed_fields(self) -> list["DocumentVerificationField"]:
        return [field for field in self.fields if not field.agreed]

    def __repr__(self) -> str:
        return (
            f"<DocumentVerification {self.id} "
            f"status={self.status.value if self.status else None} "
            f"confidence={self.confidence}>"
        )


class DocumentVerificationField(Base, UUIDMixin, TimestampMixin):
    """One extracted field, and what each agent said about it."""

    __tablename__ = "document_verification_fields"

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_document_verification_fields_confidence_ratio",
        ),
        CheckConstraint(
            "jsonb_typeof(agent_values) = 'array'",
            name="ck_document_verification_fields_agent_values_is_array",
        ),
        CheckConstraint(
            "agreed = (disagreement_kind IS NULL)",
            name="ck_document_verification_fields_kind_matches_agreed",
        ),
        UniqueConstraint(
            "verification_id",
            "field_path",
            name="uq_document_verification_fields_verification_field",
        ),
        Index(
            "ix_document_verification_fields_disagreed",
            "verification_id",
            postgresql_where=text("agreed = false"),
        ),
    )

    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_verifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    agreed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    consensus_value: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    agent_values: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    disagreement_kind: Mapped[Optional[DisagreementKind]] = mapped_column(
        PGEnum(
            DisagreementKind,
            name=DISAGREEMENT_KIND_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=True,
    )
    resolved_value: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    verification: Mapped[DocumentVerification] = relationship(
        "DocumentVerification", back_populates="fields"
    )

    @property
    def effective_value(self) -> Any:
        return self.resolved_value if self.resolved_value is not None else self.consensus_value

    def __repr__(self) -> str:
        return (
            f"<DocumentVerificationField {self.field_path} "
            f"agreed={self.agreed} confidence={self.confidence}>"
        )


__all__ = [
    "BLOCKING_STATUSES",
    "DISAGREEMENT_KIND_ENUM_NAME",
    "RELEASING_STATUSES",
    "TERMINAL_VERIFICATION_STATUSES",
    "VERIFICATION_STATUS_ENUM_NAME",
    "DisagreementKind",
    "DocumentVerification",
    "DocumentVerificationField",
    "VerificationStatus",
]
