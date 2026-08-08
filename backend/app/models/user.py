"""
Database representation of the system User entity for FlowPilot AI.

Defines account credentials, authentication indexes, platform authorization flags,
and bidirectional relationship mappings targeting work items, rules, notifications,
and conversational memory blocks.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.ai_settings import AISettings
    from app.models.assistant import Conversation
    from app.models.automation import AutomationRule
    from app.models.email_settings import EmailSettings
    from app.models.notification import Notification
    from app.models.organization import OrganizationMember
    from app.models.work_item import WorkItem
    from app.models.workspace import WorkspaceMember


class User(Base, UUIDMixin, TimestampMixin):
    """
    Persistent representation of a user identity within FlowPilot AI.

    Inherits UUID primary keys and automated timezone audit tracking timestamps.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Identity lifecycle (ARCH-03)
    # ------------------------------------------------------------------
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        # Deliberately unindexed. Verification is checked against the already
        # loaded User row, never queried across users; an index here would be
        # written on every registration and read by nothing.
        doc=(
            "When this address was proved to be controlled by its owner. "
            "NULL means unverified, and stays a permitted value: it is how a "
            "newly registered account is represented. Existing accounts are "
            "backfilled to created_at by the ARCH-03 MIGRATE revision (§B.4). "
            "Unverified users may log in and read /me/context; they may not "
            "accept an invitation or reach any workspace-scoped route (§B.4)."
        ),
    )

    sessions_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "Global session cutoff. Any access token whose iat predates this "
            "value is rejected, which makes password reset, sign-out-everywhere, "
            "and deactivation take effect immediately rather than at the end "
            "of the access TTL (§B.6). The check is free because "
            "get_current_active_user already holds this row; the alternative "
            "was a session lookup on every request. NULL means never revoked."
        ),
    )

    # Keep these relationships
    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="user",
        foreign_keys="WorkspaceMember.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        "OrganizationMember",
        back_populates="user",
        foreign_keys="OrganizationMember.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )