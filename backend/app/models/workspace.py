"""
Database representation of the Workspace collaboration boundary for
FlowPilot AI.

A Workspace belongs to exactly one Organization and is where operational data
lives: documents, conversations, automation rules, and workspace settings.
Commercial concerns (subscription, seats, SSO, audit retention) belong to the
Organization above it.

WorkspaceRole deliberately has no OWNER. A workspace does not own itself; the
organization owns it. This mirrors GitHub, where repositories have an `admin`
permission level but no owner role, ownership residing with the organization.
Retaining a workspace-level owner would create two competing ownership
concepts, and every future billing or deletion question would have to
disambiguate them.
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
    from app.models.user import User


# ============================================================================
# Enumerations
# ============================================================================

class WorkspaceRole(str, Enum):
    """
    Workspace-level access grants.

    ADMIN         full control of this workspace: settings, member grants,
                  automation, archival
    CONTRIBUTOR   create, edit, and process documents; use the AI assistant
    VIEWER        read-only

    Organization OWNER and ADMIN receive an implicit, derived ADMIN grant on
    every workspace in their organization. That elevation is resolved at
    request time in app/core/workspace_permissions.py and is never written to
    this table.
    """
    ADMIN = "ADMIN"
    CONTRIBUTOR = "CONTRIBUTOR"
    VIEWER = "VIEWER"


class WorkspaceStatus(str, Enum):
    """
    Lifecycle state of a collaboration boundary.

    ARCHIVED is a soft delete: the workspace and its data are retained and
    restorable within the organization's retention window.
    SUSPENDED is an administrative or billing block and is reversible.
    """
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    SUSPENDED = "SUSPENDED"


# ============================================================================
# Models
# ============================================================================

class WorkspaceMember(Base, UUIDMixin, TimestampMixin):
    """
    Grants a user access to a workspace at a specific role.

    A WorkspaceMember row may only exist where a corresponding ACTIVE
    OrganizationMember row exists for the same (user_id, organization_id).
    That invariant is enforced in the service layer and asserted by the
    isolation test suite; it is not expressible as a database constraint
    without denormalizing organization_id onto this table.
    """
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
        doc="Timestamp at which this grant entered DEACTIVATED.",
    )
    deactivated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Actor who revoked this grant. SET NULL preserves the record.",
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
    """
    Persistent workspace configuration and collaboration boundary.

    Ownership is NOT stored on this model, and is no longer expressible here:
    ownership is an organization-level concept. A workspace is administered by
    every WorkspaceMember row referencing it with role = WorkspaceRole.ADMIN,
    plus every OrganizationMember of its parent organization holding
    OrganizationRole.OWNER or OrganizationRole.ADMIN.

    Locale and branding live here rather than on the Organization because a US
    and an India workspace on a single contract legitimately need different
    currency, timezone, and date formatting.
    """
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
        doc=(
            "URL-safe identifier, unique within the parent organization. "
            "Combined with the organization slug this yields the public "
            "address /{organization}/{workspace}/..."
        ),
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
        String(64),
        default="UTC",
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(10),
        default="en",
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        default="USD",
        nullable=False,
    )
    date_format: Mapped[str] = mapped_column(
        String(20),
        default="YYYY-MM-DD",
        nullable=False,
    )
    company_logo_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
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