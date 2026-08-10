"""
ARCH-04 invitation lifecycle: the organization-scoped invitation and its
attached workspace grants.

Replaces workspace_invitations, which named a single workspace and a single
WorkspaceRole because that was all an invitation could ever carry. The finding
that forced the rewrite (§A.2.1): the seat is the organization, but the old
invitation was the workspace, so there was no way to invite an organization
ADMIN and no way to invite a BILLING manager at all — the role ARCH-01
introduced specifically because its absence is a recurring procurement
blocker.

An OrganizationInvitation now names an organization, an organization role, and
zero or more workspace grants (§B.1). Zero grants is how a BILLING manager is
onboarded and is a first-class case, not an edge case — it is most of the
reason this file exists.

Grants live in a child table rather than a JSON column (§B.2): a JSON blob has
no foreign key, so a grant survives the deletion of the workspace it
references; no enum, so an invalid role surfaces at acceptance instead of at
insert; and no index, so "which pending invitations touch this workspace"
becomes a sequential scan. ON DELETE CASCADE runs from both parents — a
workspace deleted between issuance and acceptance takes its grant with it, and
acceptance simply provisions fewer grants than the invitation was issued with,
which is correct and which Step 6's acceptance response reports (R8).

This is a NEW table, not workspace_invitations renamed (§B.3): workspace_id
must leave, and "workspace_invitations" would otherwise lie about its own
contents for the rest of the product's life. Row identifiers are carried
across unchanged during Step 4's backfill, so any existing audit reference to
an invitation id continues to resolve after CONTRACT.

Step 2 declares this table; it is not yet migrated into any database. Step 3
(EXPAND) is what actually runs the CREATE TABLE, by hand, against the schema
this file describes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.organization import OrganizationRole
from app.models.workspace import WorkspaceRole

# Borrowed for exactly one phase. §D2.2: the Postgres type this backs,
# invitation_status, already carries the five states this table needs, and
# reusing it means Step 3's EXPAND creates no new enum type here. When
# workspace_invitations.py is deleted at Step 5 CONTRACT, this class moves
# into this file (or a shared enums module) and this import is updated —
# tracked in this file's own exit criteria so it is not lost between steps.
from app.models.workspace_invitation import InvitationStatus

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


# ============================================================================
# Models
# ============================================================================

class OrganizationInvitation(Base, UUIDMixin, TimestampMixin):
    """
    An invitation to join an organization, carrying zero or more workspace
    grants.

    Unidirectional toward Organization and User (§D2.6), matching the house
    rule stated in workspace_invitation.py: a relationship that would require
    editing organization.py or user.py to add a back-reference is not added in
    a step scoped to this file. The one exception is `grants`, back-populated
    from InvitationWorkspaceGrant — both models are new in this same step, so
    the constraint that motivates unidirectionality elsewhere does not apply
    between them, and §B.2 needs `invitation.grants` to be a plain iterable at
    acceptance.
    """
    __tablename__ = "organization_invitations"
    __table_args__ = (
        # §B.9. lower(email) because the service normalizes to lowercase
        # before insert; the index must agree with that normalization or one
        # inconsistent write path is enough to seat the same person twice.
        Index(
            "uq_pending_organization_invitation",
            "organization_id",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Step 7 — GET /organizations/{id}/invitations, filterable by status.
        Index(
            "ix_organization_invitations_organization_status",
            "organization_id",
            "status",
        ),
        # Step 8 — the sweeper's WHERE status = 'PENDING' AND expires_at < now().
        Index(
            "ix_organization_invitations_status_expires_at",
            "status",
            "expires_at",
        ),
        # §B.4 — OWNER is not invitable. Enforced here as well as in the service layer.
        CheckConstraint(
            "organization_role <> 'OWNER'",
            name="role_not_owner",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "NOT NULL from creation. Unlike workspace_invitations.organization_id "
            "under ARCH-01/03, this table is new and has no pre-existing rows "
            "to backfill (§A.2.3), so there is no nullable-then-tightened "
            "phase to carry."
        ),
    )
    inviter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "CASCADE, matching the workspace_invitations precedent: deleting "
            "the inviter's account removes invitations they sent rather than "
            "leaving an orphaned reference to a user who no longer exists."
        ),
    )
    invited_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "§B.5. Bound at issuance where the address already has an "
            "account, and at acceptance otherwise."
        ),
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Normalized to lowercase by the service before insert (Step 6).",
    )
    organization_role: Mapped[OrganizationRole] = mapped_column(
        PgEnum(
            OrganizationRole,
            name="organization_role",
            create_type=False,
        ),
        nullable=False,
        doc="One of ADMIN, BILLING, MEMBER. OWNER is excluded by role_not_owner constraint.",
    )
    status: Mapped[InvitationStatus] = mapped_column(
        PgEnum(
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
        doc="SHA-256 of the invitation secret, hex encoded, always 64 characters.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="The actor who revoked this invitation.",
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Set at issuance and at every resend.",
    )
    send_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        doc="Counts send attempts. Starts at 1.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    inviter: Mapped["User"] = relationship(
        "User",
        foreign_keys=[inviter_id],
    )
    invited_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[invited_user_id],
    )
    revoked_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[revoked_by_id],
    )
    grants: Mapped[list["InvitationWorkspaceGrant"]] = relationship(
        "InvitationWorkspaceGrant",
        back_populates="invitation",
        cascade="all, delete-orphan",
        doc="The workspace grants attached to this invitation.",
    )


class InvitationWorkspaceGrant(Base, UUIDMixin, TimestampMixin):
    """
    One workspace-and-role pair attached to an OrganizationInvitation.
    """
    __tablename__ = "invitation_workspace_grants"
    __table_args__ = (
        UniqueConstraint(
            "invitation_id",
            "workspace_id",
            name="uq_invitation_workspace_grant",
        ),
    )

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_invitations.id", ondelete="CASCADE"),
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
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    invitation: Mapped["OrganizationInvitation"] = relationship(
        "OrganizationInvitation",
        back_populates="grants",
    )
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )