from __future__ import annotations
import uuid
from typing import TYPE_CHECKING, Any, Optional, Union

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class DocumentSettings(Base, UUIDMixin, TimestampMixin):
    """
    Persistent document settings owned by a single workspace.
    """
    __tablename__ = "document_settings"
    __table_args__ = (
        CheckConstraint(
            "chunk_size_tokens BETWEEN 32 AND 254",
            name="chunk_size_tokens_range",
        ),
        CheckConstraint(
            "chunk_overlap_pct BETWEEN 0 AND 40",
            name="chunk_overlap_pct_range",
        ),
        CheckConstraint(
            "embedding_model = 'sentence-transformers/all-MiniLM-L6-v2'",
            name="embedding_model_pinned",
        ),
        CheckConstraint(
            "intent_config IS NULL OR jsonb_typeof(intent_config) = 'object'",
            name="ck_document_settings_intent_config_object",
        ),
    )

    #: ARCH-11 Step 3: Token-aware chunking configuration
    chunk_size_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=220,
        server_default="220",
    )

    chunk_overlap_pct: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default="10",
    )

    #: ARCH-11.5 Step 4: Per-workspace intent configuration
    intent_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="ARCH-11.5. {intent: [keyword, ...]}. NULL follows platform defaults.",
    )

    #: DEPRECATED (ARCH-11 Step 3). Character-based. Dropped in Step 9 CONTRACT.
    chunk_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=500,
    )

    chunk_overlap: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="sentence-transformers/all-MiniLM-L6-v2",
    )

    ocr_language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="eng",
    )

    max_upload_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
    )

    allowed_file_types: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="pdf,png,jpg,jpeg",
    )

    duplicate_detection: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    automatic_classification: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    automatic_summarization: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    automatic_entity_extraction: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    updated_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")

    updated_by: Mapped[Union["User", None]] = relationship("User")