"""
Database representation of the Organization tenant root for FlowPilot AI.
ARCH-14 Step 4: Quota tier version foreign key pointer.
ARCH-20: Data residency region.
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
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.models.quota_tier import QuotaTier


class OrganizationRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    BILLING = "BILLING"
    MEMBER = "MEMBER"


class OrganizationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class MembershipStatus(str, Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class Organization(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organizations"

    __table_args__ = (
        CheckConstraint(
            "seat_limit IS NULL OR seat_limit >= 1",
            name="seat_limit_positive",
        ),
        CheckConstraint(
            "data_residency_region IN ('US', 'EU', 'APAC', 'GLOBAL')",
            name="ck_organizations_data_residency_region_vocabulary",
        ),
    )

    slug: Mapped[str] = mapped_column(
        String(63),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    legal_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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
    seat_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    data_residency_region: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'GLOBAL'"),
        default="GLOBAL",
        index=True,
    )

    quota_tier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quota_tiers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    quota_tier: Mapped["QuotaTier | None"] = relationship("QuotaTier")
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
    )
    deactivated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

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