"""
Database representation of the Workspace collaboration boundary for
FlowPilot AI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.organization import MembershipStatus

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.uploaded_file import UploadedFile
    from app.models.user import User


# ============================================================================
# Enumerations
# ============================================================================

class WorkspaceRole(str, Enum):
    ADMIN = "ADMIN"
    CONTRIBUTOR = "CONTRIBUTOR"
    VIEWER = "VIEWER"


class WorkspaceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    SUSPENDED = "SUSPENDED"


# ============================================================================
# Models
# ============================================================================

class WorkspaceMember(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_id",
            name="uq_user_workspace_membership",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        PgEnum(
            WorkspaceRole,
            name="workspace_role",
            create_type=False,
        ),
        default=WorkspaceRole.VIEWER,
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        PgEnum(
            MembershipStatus,
            name="membership_status",
            create_type=False,
        ),
        default=MembershipStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deactivated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="memberships",
        foreign_keys=[user_id],
    )
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="members",
    )
    deactivated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[deactivated_by_id],
    )


class Workspace(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_workspace_organization_slug",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(
        String(63),
        nullable=False,
        index=True,
    )
    workspace_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        PgEnum(
            WorkspaceStatus,
            name="workspace_status",
            create_type=False,
        ),
        default=WorkspaceStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    # --- Localization and branding -----------------------------------------
    timezone: Mapped[str] = mapped_column(
        String(100),
        default="UTC",
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(20),
        default="en",
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        default="USD",
        nullable=False,
    )
    date_format: Mapped[str] = mapped_column(
        String(30),
        default="YYYY-MM-DD",
        nullable=False,
    )
    company_logo_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    logo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
        doc="Durable link to adopted logo in uploaded_files (ARCH-07 Step 6).",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="workspaces",
    )
    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    logo_file: Mapped["UploadedFile | None"] = relationship(
        "UploadedFile",
        foreign_keys=[logo_file_id],
    )