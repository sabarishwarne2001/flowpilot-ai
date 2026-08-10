from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.workspace import WorkspaceRole
from app.models.organization_invitation import InvitationStatus  # noqa: F401

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.models.organization import Organization


class WorkspaceInvitation(Base, UUIDMixin, TimestampMixin):
    """
    Represents an invitation sent to an external email address 
    to join a FlowPilot AI workspace under a specified role.
    """
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        Index(
            "uq_pending_invitation",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'PENDING'")
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc=(
            "Parent organization of the invited workspace. Nullable during "
            "ARCH-01 and populated by the MIGRATE revision. ARCH-04 makes "
            "this non-nullable when invitations become organization-scoped "
            "with workspace grants attached, matching how GitHub invites to "
            "an organization and then to teams."
        ),
    )
    inviter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
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
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(
            InvitationStatus,
            name="invitation_status",
            create_type=False,
        ),
        default=InvitationStatus.PENDING,
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc=(
            "SHA-256 of the invitation secret, hex encoded, always 64 "
            "characters."
        ),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )
    organization: Mapped["Organization | None"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    inviter: Mapped["User"] = relationship(
        "User",
        foreign_keys=[inviter_id],
    )