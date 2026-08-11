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

    # ------------------------------------------------------------------
    # Profile (ARCH-05 §A.2.4, §B.4)
    # ------------------------------------------------------------------
    #
    # Before this, `users` had no profile at all — no name, no timezone, no
    # locale. UserSummary said so in as many words: "Mirrors the User model
    # exactly: it has no display-name column, so none is declared here." The
    # sidebar rendered `user?.email`, the member directory showed raw
    # addresses, and ARCH-04's invitation templates passed
    # `inviter_display=inviter.email` because there was nothing else to pass.
    #
    # `avatar_url` was considered and deliberately excluded (§B.4). Upload
    # infrastructure already exists elsewhere in the product, so it is
    # possible — but it brings image validation, storage lifecycle, and a
    # moderation question that belong to their own change, not to a phase
    # about ownership transfer and profile immutability.
    #
    # No columns were added, renamed, or retyped on this table by ARCH-05
    # beyond these three. In particular `email` is untouched: A.2.5 found
    # that email mutability was an absence rather than a decision, and §B.5
    # keeps it that way deliberately — see EmailImmutableError at the
    # PATCH /me/profile boundary (Step 5). Nothing below changes that.

    display_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc=(
            "User-chosen display name, shown in the member directory, audit "
            "lines, and mail sent to or about this person (§B.6). "
            "NULL rather than defaulted from the email local-part: a NULL "
            "that a caller renders as the email address is honest, where a "
            "derived default ('jane' from 'jane@example.com') is a guess "
            "the product would then treat as a fact. Every read site is "
            "expected to fall back to `email` when this is NULL, not to "
            "invent a name."
        ),
    )

    timezone: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="UTC",
        doc=(
            "IANA timezone identifier (e.g. 'America/New_York'), used to "
            "localize timestamps shown to this user. NOT NULL — every "
            "account has a timezone whether or not it was ever chosen, and "
            "'UTC' is the honest default for one that was not. Format is "
            "validated at the PATCH /me/profile boundary (Step 5), not "
            "here; the column itself accepts any string that fits, matching "
            "the rest of this model's division of labour between schema "
            "and service."
        ),
    )

    locale: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="en",
        doc=(
            "BCP 47 language tag (e.g. 'en', 'pt-BR'), reserved for future "
            "localized output. Bounded at 20 rather than 100 like the two "
            "columns above it: the longest realistic tag "
            "(language-Script-REGION-variant) does not approach that "
            "length, and a column this narrow cannot silently accept "
            "something that was never a locale."
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