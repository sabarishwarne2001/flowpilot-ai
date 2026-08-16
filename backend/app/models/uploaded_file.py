"""
ARCH-06 Step 5 — uploaded_files: the ownership record Step 1b stood in for.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


class UploadedFile(Base, UUIDMixin, TimestampMixin):
    """
    Ownership and lifecycle record for one file written to storage.
    """

    __tablename__ = "uploaded_files"

    __table_args__ = (
        Index(
            "ix_uploaded_files_owner_id",
            "owner_id",
        ),
        Index(
            "ix_uploaded_files_checksum_sha256",
            "checksum_sha256",
        ),
        Index(
            "ix_uploaded_files_deleted_at",
            "deleted_at",
        ),
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="The uploader. Nullable matching database schema.",
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    checksum_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    owner: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[owner_id],
    )

    organization: Mapped[Organization | None] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    workspace: Mapped[Workspace | None] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )