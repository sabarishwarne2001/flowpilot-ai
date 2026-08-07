from __future__ import annotations
import uuid
from typing import TYPE_CHECKING, Union

from sqlalchemy import Boolean, ForeignKey, Integer, String
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