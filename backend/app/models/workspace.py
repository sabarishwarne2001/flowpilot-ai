from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class WorkspaceRole(str, enum.Enum):
    """
    Available workspace membership roles within FlowPilot AI.
    """
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    CONTRIBUTOR = "CONTRIBUTOR"
    VIEWER = "VIEWER"


class WorkspaceMember(Base, UUIDMixin, TimestampMixin):
    """
    Represents the many-to-many relationship mapping users to workspaces.
    """
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_user_workspace_membership"),
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
        Enum(
            WorkspaceRole,
            name="workspace_role",
            create_type=False,
        ),
        default=WorkspaceRole.VIEWER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="memberships",
    )
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="members",
    )


class Workspace(Base, UUIDMixin, TimestampMixin):
    """
    Persistent workspace configuration owned by a single user.
    """

    __tablename__ = "workspaces"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    workspace_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    company_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    company_logo_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Regional
    # ------------------------------------------------------------------

    timezone: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="UTC",
    )

    language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="en",
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USD",
    )

    date_format: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="YYYY-MM-DD",
    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user: Mapped["User"] = relationship(
        "User",
        back_populates="workspace",
        passive_deletes=True,
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )