"""
Database representation of the Organization tenant root for FlowPilot AI.

The Organization is the commercial tenant: the entity that holds the
subscription, consumes seats, owns verified domains and security policy, and
retains the audit trail. It sits above the Workspace, which is the
collaboration boundary where operational data lives.

This separation mirrors GitHub (Organization above Repository), Slack
Enterprise Grid (Organization above Workspace), and Linear (Organization above
Team). Conflating the two is a one-way door: the moment a customer wants a
second workspace on one contract, a flat model must either bill them twice or
be migrated under a live agreement.

OrganizationMember is the seat unit. A user belonging to five workspaces of the
same organization consumes exactly one seat.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


# ============================================================================
# Enumerations
# ============================================================================

class OrganizationRole(str, Enum):
    """
    Organization-level roles.

    Distinct from WorkspaceRole: these govern the commercial and identity
    surface (billing, seats, SSO, audit), not day-to-day content access.

    BILLING exists because in mid-market and enterprise deals the person
    holding the payment method is typically a finance controller who must
    never see customer documents. GitHub ships a Billing Manager role for the
    same reason, and its absence is a recurring procurement blocker.
    """
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    BILLING = "BILLING"
    MEMBER = "MEMBER"


class OrganizationStatus(str, Enum):
    """
    Lifecycle state of a commercial tenant.

    SUSPENDED covers non-payment and policy enforcement and is reversible.
    ARCHIVED is a soft-deleted tenant retained for the contractual data
    retention window before purge.
    """
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class MembershipStatus(str, Enum):
    """
    Lifecycle state of a membership, shared by organization and workspace
    memberships.

    Replaces the previous is_active boolean, which could not distinguish
    "invited but not yet accepted" from "removed by an administrator" from
    "temporarily suspended".

    Permitted transitions:

        INVITED ──► ACTIVE ──► SUSPENDED ──► ACTIVE
                       │            │
                       └────────────┴──► DEACTIVATED   (terminal)

    DEACTIVATED is terminal and the row is retained, never deleted. This
    preserves attribution for past work, gives the audit log a stable subject,
    and makes re-adding a former member traceable.
    """
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


# ============================================================================
# Models
# ============================================================================

class Organization(Base, UUIDMixin, TimestampMixin):
    """
    The commercial tenant.

    Owns the subscription, the seat pool, verified domains, security policy,
    and the audit trail. Operational data (documents, conversations,
    automation) belongs to its Workspaces, never directly to the Organization.

    Ownership is not stored on this model. An organization is owned by every
    OrganizationMember row referencing it with role = OrganizationRole.OWNER
    and status = MembershipStatus.ACTIVE. At least one such row must always
    exist; the invariant is enforced in the service layer.
    """
    __tablename__ = "organizations"

    # ARCH-04 §D2.5. Explicit __table_args__, name passed to naming convention
    __table_args__ = (
        CheckConstraint(
            "seat_limit IS NULL OR seat_limit >= 1",
            name="seat_limit_positive",
        ),
    )

    slug: Mapped[str] = mapped_column(
        String(63),
        nullable=False,
        unique=True,
        index=True,
        doc=(
            "Globally unique, URL-safe public identifier. Constrained to the "
            "DNS label grammar so that subdomain-based tenant addressing "
            "remains available without a second migration."
        ),
    )
    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        doc="Human-readable organization name shown throughout the product.",
    )
    legal_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Registered legal entity name, used on invoices and contracts.",
    )
    status: Mapped[OrganizationStatus] = mapped_column(
        PgEnum(
            OrganizationStatus,
            name="organization_status",
            create_type=False,
        ),
        default=OrganizationStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    # --- ARCH-04 Step 2: seat ceiling ------------------------------------
    seat_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc=(
            "Maximum active OrganizationMember rows this tenant may hold. "
            "NULL means unlimited (§B.8) — the plan's assumption, confirmed "
            "at Step 0."
        ),
    )

    # Relationships
    members: Mapped[list["OrganizationMember"]] = relationship(
        "OrganizationMember",
        back_populates="organization",
        foreign_keys="OrganizationMember.organization_id",
        cascade="all, delete-orphan",
    )
    workspaces: Mapped[list["Workspace"]] = relationship(
        "Workspace",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrganizationMember(Base, UUIDMixin, TimestampMixin):
    """
    Maps a user to an organization with a commercial role.
    """
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_user_membership",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[OrganizationRole] = mapped_column(
        PgEnum(
            OrganizationRole,
            name="organization_role",
            create_type=False,
        ),
        default=OrganizationRole.MEMBER,
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
        doc="Timestamp at which this membership entered DEACTIVATED.",
    )
    deactivated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Actor who deactivated this membership.",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="members",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="organization_memberships",
        foreign_keys=[user_id],
    )
    deactivated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[deactivated_by_id],
    )