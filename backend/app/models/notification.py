"""
ARCH-06 Step 3 (EXPAND) — organization_id on notifications, workspace_id
loosened to nullable.

This file predates the ARCH-numbered discipline the rest of this codebase now
follows (188ffda2ce99, "expand notifications schema" — no ARCH prefix, no
rationale docstring). This revision brings the SCHEMA up to §B.4's approved
shape without rewriting the parts of the model this step does not touch;
NotificationType/Channel/Status/Priority and their columns are unchanged.

WHY organization_id AT ALL (§B.4, approved Option A)
------------------------------------------------------
A.1.2/A.1.3 found every one of the 15 notification rows in the audited
database scoped to a workspace only, with no path to an organization-level
event: a seat-limit warning, a billing notice, an ownership-transfer proposal
(see `ownership_transfer.py`) — none of these belongs to one workspace, and
today's schema has nowhere to put them. Option A adds the missing column
rather than routing every organization event through some nominated
"default" workspace, which would make workspace_id lie about what the event
actually concerns.

WHY workspace_id BECOMES NULLABLE IN THIS SAME STEP, NOT LATER
-----------------------------------------------------------------
Loosening a NOT NULL constraint is additive — it rejects nothing that today's
schema didn't already accept, and is safe to ship in the same EXPAND phase as
a new nullable column, for the identical reason both changes are EXPAND and
not CONTRACT: neither can reject a write that succeeds today. Waiting until
Step 5 to loosen it would gain nothing and would only mean two migrations
touching this column instead of one. What Step 5 CONTRACT actually enforces —
and what this step deliberately does NOT — is the constraint this step's
nullability makes representable but does not yet require:

    (workspace_id IS NOT NULL) OR (organization_id IS NOT NULL)

That CHECK constraint has no reason to exist yet. Every row written by
existing code between this migration and Step 4's MIGRATE still carries a
real workspace_id — `create_notification` (app/crud/notification.py) requires
it as a keyword argument today and this step does not touch that function.
Adding the CHECK now would enforce an invariant no code has been given the
means to violate, and would need loosening again the moment Step 6 actually
starts writing organization-level rows with workspace_id NULL. It lands in
Step 5, against a table Step 4 has already backfilled, over the shape this
step only makes legal.

WHAT REMAINS DELIBERATELY UNCHANGED IN THIS STEP
----------------------------------------------------
`ix_notifications_workspace_user_read_created` is untouched. It still serves
every existing workspace-scoped notification list, and dropping or narrowing
it now — before Step 6 has written a single organization-level row — would
degrade a live query path for a benefit that does not exist yet.
`app/crud/notification.py`'s functions are untouched for the same reason:
they are workspace-only today and stay workspace-only until Step 6 is the
step that teaches them about organization-level rows. This file adds what
Step 6 will need without changing what today's code already relies on.

ARCH-06 STEP 5 (CONTRACT) UPDATE
-----------------------------------
The CHECK constraint anticipated above by this docstring's own quoted line
now exists, in `__table_args__` below, named `has_scope`. It is the OR form
quoted here, not the stricter `organization_id NOT NULL` the approved plan's
one-line Step 5 summary names — see the constraint's own inline comment for
exactly why that stronger form is not yet safe to ship: `create_notification`
still does not set `organization_id` on any row it writes, and asserting NOT
NULL against a column its only writer never populates would fail on that
writer's next call, not on bad data.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Union

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace


class NotificationType(str, enum.Enum):
    DOCUMENT = "DOCUMENT"
    AUTOMATION = "AUTOMATION"
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"
    SECURITY = "SECURITY"


class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WEBHOOK = "WEBHOOK"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class NotificationPriority(str, enum.Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Notification(Base, UUIDMixin, TimestampMixin):
    """
    Persistent notification record.

    As of ARCH-06 Step 5, a row's scope is a workspace, an organization, or
    both — the `has_scope` CHECK constraint below enforces that it is never
    neither. It is still not necessarily both: nothing before some future
    step teaches `create_notification` to populate `organization_id`, so
    every row written by today's code carries a real `workspace_id` and a
    NULL `organization_id`, even though the column has held a backfilled
    value on every EXISTING row since Step 4. See the module docstring for
    exactly which invariant each step added.
    """
    __tablename__ = "notifications"

    __table_args__ = (
        Index(
            "ix_notifications_workspace_user_read_created",
            "workspace_id",
            "user_id",
            "is_read",
            "created_at",
        ),
        # ARCH-06 Step 3 (§B.4). Serves the organization-scoped read path
        # Step 6 introduces (an org-admin's notification list, a seat-limit
        # warning feed) the same way the workspace-scoped index above serves
        # today's only read path. Added now, ahead of any row that needs it,
        # for the same reason organization_id is added now rather than in
        # Step 6 itself: an index on a column with no rows yet is
        # instantaneous to build and never competes with production traffic
        # the way adding it later — once Step 6 has been writing rows for a
        # while — would.
        Index(
            "ix_notifications_organization_user_read_created",
            "organization_id",
            "user_id",
            "is_read",
            "created_at",
        ),
        # ARCH-06 Step 5 (CONTRACT). Every row must name at least one scope.
        #
        # This is deliberately the OR form, not organization_id NOT NULL —
        # which is what the approved plan's own one-line Step 5 summary says
        # ("organization_id -> NOT NULL; workspace_id -> nullable") and
        # which this constraint does NOT implement. That summary describes
        # where this column is eventually headed once something writes
        # organization_id on every row; it does not hold today.
        # create_notification (app/crud/notification.py) is UNCHANGED by
        # this step and still constructs every Notification without setting
        # organization_id at all — meaning every row it writes between this
        # migration landing and whatever future step teaches it about
        # organization_id would have organization_id NULL. A NOT NULL
        # constraint here would make that column's own only writer fail on
        # its very next INSERT: a self-inflicted outage, not a data
        # invariant. The OR form is satisfied by workspace_id alone, which
        # create_notification has always set, so it enforces the one thing
        # actually true of every row that exists or that today's code can
        # produce — "this row has SOME scope" — without asserting a stronger
        # claim the write path cannot yet back up. Tightening this to
        # organization_id NOT NULL is a future CONTRACT step's job, gated on
        # first updating create_notification (or its Step 6+ successor) to
        # populate organization_id unconditionally.
        CheckConstraint(
            "workspace_id IS NOT NULL OR organization_id IS NOT NULL",
            name="has_scope",
        ),
    )

    title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        SQLEnum(
            NotificationType,
            name="notification_type",
            create_type=False,
        ),
        nullable=False,
        default=NotificationType.SYSTEM,
        index=True,
    )

    priority: Mapped[NotificationPriority] = mapped_column(
        SQLEnum(
            NotificationPriority,
            name="notification_priority",
            create_type=False,
        ),
        nullable=False,
        default=NotificationPriority.INFO,
        index=True,
    )

    delivery_channel: Mapped[NotificationChannel] = mapped_column(
        SQLEnum(
            NotificationChannel,
            name="notification_channel",
            create_type=False,
        ),
        nullable=False,
        default=NotificationChannel.IN_APP,
        index=True,
    )

    delivery_status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(
            NotificationStatus,
            name="notification_status",
            create_type=False,
        ),
        nullable=False,
        default=NotificationStatus.PENDING,
        index=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        doc=(
            "NULLABLE as of ARCH-06 Step 3 (§B.4) — was NOT NULL. Every row "
            "written by today's code still sets this; create_notification "
            "(app/crud/notification.py) requires it and is unchanged by this "
            "step. NULL becomes meaningful only once Step 6 writes an "
            "organization-level row, at which point organization_id is what "
            "carries the row's scope instead. See the module docstring for "
            "why the loosening lands here rather than in Step 5, and for the "
            "(workspace_id IS NOT NULL) OR (organization_id IS NOT NULL) "
            "invariant Step 5 adds once Step 4 has made it true of every row."
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        doc=(
            "ARCH-06 Step 3 (§B.4). NULL for every row that exists as of "
            "this migration — see the module docstring. CASCADE, matching "
            "workspace_id's existing ondelete and every other tenant-scope "
            "FK in this schema (Workspace.organization_id, "
            "OwnershipTransfer.organization_id): a deleted organization "
            "takes its notifications with it. Populated going forward by "
            "Step 6 for organization-level events, and backfilled onto "
            "EXISTING workspace-scoped rows by Step 4's MIGRATE (derived "
            "from workspace_id -> workspaces.organization_id, since a row "
            "scoped to a workspace also belongs to that workspace's "
            "organization) — Step 4 is what makes this column NOT NULL for "
            "every row while workspace_id remains NOT NULL for the subset "
            "that is actually workspace-scoped."
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "work_items.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    # Both unidirectional — no `Workspace.notifications` or
    # `Organization.notifications` collection. Matches the ARCH-02 discipline
    # stated on auth_token.py and ownership_transfer.py: a back reference
    # would load an unbounded, unfiltered collection by default on every
    # access to the parent, and this table has the same shape of risk those
    # docstrings describe. workspace is Optional to match the column's new
    # nullability; organization is added fresh, also Optional, for the
    # identical reason.
    workspace: Mapped["Workspace | None"] = relationship("Workspace")

    organization: Mapped["Organization | None"] = relationship("Organization")

    user: Mapped["User"] = relationship(
        "User",
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="notifications",
        passive_deletes=True,
    )