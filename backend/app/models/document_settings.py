from __future__ import annotations
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base
from app.db.base import TimestampMixin
from app.db.base import UUIDMixin


class DocumentSettings(Base, UUIDMixin, TimestampMixin):
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

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    user = relationship("User", back_populates="document_settings")