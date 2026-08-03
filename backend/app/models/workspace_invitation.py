from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.workspace import WorkspaceRole

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class InvitationStatus(str, enum.Enum):
    """
    Available states for a workspace invitation.
    """
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class WorkspaceInvitation(Base, UUIDMixin, TimestampMixin):
    """
    Represents an invitation sent to an external email address 
    to join a FlowPilot AI workspace under a specified role.
    """
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        # Production-grade partial unique index: ensures that at most ONE invitation 
        # is active ('PENDING') per email per workspace at any given time.
        # This allows users to be re-invited if their previous invites expired, 
        # were rejected, or revoked, while preventing concurrent duplicates.
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
    # Production-safe string length of 512 is utilized to handle future invitation token strategies,
    # including high-entropy secure hashes or standard signed JWT payloads.
    token: Mapped[str] = mapped_column(
        String(512),
        unique=True,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    
    # Audit timestamps mapping the full lifecycle of the invitation
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

    # ------------------------------------------------------------------
    # Unidirectional Relationships
    # ------------------------------------------------------------------
    # Unidirectional relationships are intentionally utilized for the current roadmap stage.
    # While bidirectional back_populates to User and Workspace are architecturally elegant,
    # adding them requires modifying those models directly, which would violate our constraint
    # of editing ONLY the requested file in a single step. Unidirectional mapping remains 100% 
    # compatible, prevents model lookup errors, and is easily upgraded when those models are modified.
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )
    inviter: Mapped["User"] = relationship(
        "User",
        foreign_keys=[inviter_id],
    )