"""
ARCH-05 two-phase ownership transfer.

Closes A.2.2. `organization_member_service.transfer_ownership` (ARCH-01,
locked against A.2.1 by ARCH-05 Step 1) still does the actual promotion —
this table is not a replacement for it, it is the consent gate in front of
it. Nothing in `transfer_ownership` changes here or is called from here.
Step 6 is what makes accepting a row in this table invoke it.

WHY A ROW AT ALL, WHEN THE PRODUCT ALREADY HAS `transfer_ownership` (§B.1)
    The target does not agree, is not asked, and is not told. Ownership
    carries `seat_limit` authority today and billing liability under Phase F,
    so "make someone financially responsible for a tenant with one click of
    someone else's" is not a role change, it is closer to co-signing a loan
    on their behalf. `ownership_transfers` is the record of the target's
    agreement — or refusal — to that. Two states describe an event that
    already happened (transfer_ownership's own UPDATEs); PENDING and its
    resolutions describe an event that has not happened yet and might not.

WHY `target_membership_id`, NOT `target_user_id`
    Every existing owner-set function — `transfer_ownership`,
    `change_member_role`, `deactivate_member`, `leave_organization` — takes
    an `OrganizationMember`, not a `User`, because the role and status being
    reasoned about live on the membership, not the account. A transfer
    proposal is a claim about one specific membership in one specific
    organization, and storing that FK directly means Step 6 loads exactly
    the object `transfer_ownership` already expects, with no second lookup
    reconstructing "which membership did this row mean." It also means a
    proposal cannot silently outlive the membership it targets — see
    `ondelete="CASCADE"` below.

    `initiated_by_id` stays a plain `users.id` FK. The initiator's
    membership is re-derived at request time in Step 6 anyway (their role
    must be re-checked, per the ARCH-05 Step 1 lock-and-refresh discipline,
    not trusted from whenever this row was written), so nothing is gained by
    storing a membership FK for them, and `initiated_by_id` reads correctly
    on its own in an audit line the way `target_membership_id` alone would
    not.

WHY THIS FILE, NOT `organization.py`
    Matches `organization_invitation.py`: a new lifecycle table with its own
    enum gets its own file, importing `Organization` and `OrganizationMember`
    only under `TYPE_CHECKING`, unidirectionally. Editing `organization.py`
    or `user.py` to add a back-reference is out of scope for a change
    confined to this file, same house rule stated there. Neither `User` nor
    `OrganizationMember` gains an `ownership_transfers` relationship;
    Step 6 queries this table directly by `target_membership_id` or
    `organization_id`, which is all either read path needs.

Step 3 declares this table; it is not yet migrated into any database. Step 4
(EXPAND, and the ONLY migration this phase has — no MIGRATE, no CONTRACT) is
what runs the CREATE TYPE and CREATE TABLE this file describes, by hand,
against a table that starts and stays empty until Step 6 exists to write to
it.

A CAUGHT ERROR, WORTH RECORDING HERE RATHER THAN ONLY IN THE PR
    The approved plan's own Step 3 gate says the mechanical FK name for
    `target_membership_id` → `organization_members` is "62, uncomfortably
    close" to PostgreSQL's 63-character limit. It is actually 64 — one over,
    not one under — computed directly from this codebase's own naming
    convention (`app/db/base.py`):

        fk_ + ownership_transfers + _ + target_membership_id + _ + organization_members
        = fk_ownership_transfers_target_membership_id_organization_members
        = 64 characters

    PostgreSQL does not error on a 64-character identifier; `NAMEDATALEN` is
    64 bytes with one reserved for the terminator, so it silently truncates
    to 63 and drops the final "s" of "members". That is a duplicate-name
    machine in waiting the moment any other constraint on this table happens
    to truncate to the same prefix, and a support incident with no error
    message pointing at it. The FK below is given an explicit, shorter name
    instead of the naming-convention default, on the exact precedent already
    in this codebase for the same situation: `organization_members`'s own
    `deactivated_by_id` FK (ARCH-01 EXPAND) is likewise named without its
    referred-table suffix, for the same reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization, OrganizationMember
    from app.models.user import User


# ============================================================================
# Enumerations
# ============================================================================

class OwnershipTransferStatus(str, PyEnum):
    """
    Lifecycle of a proposed ownership transfer.

    Backs the PostgreSQL type `ownership_transfer_status`, created fresh by
    the Step 4 EXPAND migration — unlike `organization_role` and its
    siblings, there is no prior table this reuses.

    Permitted transitions:

        PENDING ──► ACCEPTED     (terminal — transfer_ownership runs)
            │
            ├────► DECLINED      (terminal)
            ├────► CANCELLED     (terminal — withdrawn by the initiator)
            └────► EXPIRED       (terminal)

    EXPIRED is reached differently from every other terminal state on this
    enum, and differently from `InvitationStatus.EXPIRED` (ARCH-04's sibling
    concept, "set by the Step 8 sweeper"). §B.8 gives transfer proposals a
    7-day TTL enforced *lazily*, in the acceptance WHERE clause and by
    filtering list views, with no background job. Both parties to a transfer
    are existing, signed-in members — unlike an invitation sitting in a
    mailbox nobody is watching — so there is no mailbox-only state that a
    sweeper exists to reconcile, and no third cron entry earning its keep.
    Consequently a row can sit at `status = PENDING` with `expires_at` in the
    past until something touches it; `EXPIRED` is written by Step 6's service
    layer at that moment, not by a job that runs whether or not anyone is
    looking. Never derive "is this expired" from `status` alone — compare
    `expires_at` to the current time, exactly as `claim_invitation` does.
    """
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# ============================================================================
# Models
# ============================================================================

class OwnershipTransfer(Base, UUIDMixin, TimestampMixin):
    """
    A proposed transfer of organization ownership, awaiting the target's
    response.

    Existence of a row says nothing on its own about who currently owns the
    organization — that is still, and only ever, whichever `OrganizationMember`
    rows carry `role = OWNER` and `status = ACTIVE` (see `Organization`'s own
    docstring). This table is the record of an offer and its resolution nothing
    more; `transfer_ownership` remains the only code path that actually moves
    the `OWNER` role between memberships.
    """
    __tablename__ = "ownership_transfers"
    __table_args__ = (
        # §B.9 / §C Step 3. Directly the `uq_pending_organization_invitation`
        # pattern from ARCH-04, and the same reasoning: two live proposals for
        # one organization racing to acceptance is not a state anyone wants to
        # reason about, and Step 6's "does this org already have a pending
        # transfer?" check becomes a single indexed lookup against the same
        # object that enforces the invariant, rather than two things that can
        # drift apart. `status = 'PENDING'` is the enum member's own string
        # value, matching the invitation index's literal.
        Index(
            "uq_pending_ownership_transfer_per_org",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Anticipates the transfer-history view implied by §B.8 ("filtered
        # from list views") the same way organization_invitations' Step-7
        # list endpoint motivated its own (organization_id, status) index.
        # Not required by anything that exists yet in this phase; added here
        # because the partial index above cannot serve a query that needs
        # DECLINED or CANCELLED rows, and adding it later means an ALTER on a
        # table Step 6 may already be writing to.
        Index(
            "ix_ownership_transfers_organization_status",
            "organization_id",
            "status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "The tenant being transferred. CASCADE: an organization deleted "
            "with a pending transfer takes the proposal with it — there is "
            "nothing left to accept."
        ),
    )

    initiated_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "The outgoing owner who proposed the transfer, re-authenticated "
            "at initiation (§B.2). CASCADE, matching the "
            "organization_invitations.inviter_id precedent (ARCH-04): "
            "deleting the initiator's account removes proposals they made "
            "rather than leaving an orphaned reference to a user who no "
            "longer exists. Their CURRENT role is re-checked by Step 6 at "
            "acceptance time regardless of what it was when this row was "
            "written — this column identifies who asked, it does not "
            "authorize anything by itself."
        ),
    )

    target_membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization_members.id",
            ondelete="CASCADE",
            # Explicit override of the naming-convention default. See the
            # module docstring's "A caught error" section: the mechanical
            # name is 64 characters, one over PostgreSQL's limit, and would
            # be silently truncated rather than rejected. Matches the
            # existing precedent for the same situation —
            # organization_members.deactivated_by_id's FK is likewise named
            # without its referred-table suffix (ARCH-01 EXPAND).
            name="fk_ownership_transfers_target_membership_id",
        ),
        nullable=False,
        index=True,
        doc=(
            "The proposed new owner's membership in THIS organization — not "
            "their user id; see the module docstring for why. CASCADE: if "
            "the target's membership is removed before they respond — they "
            "leave, they are deactivated, their account is deleted — the "
            "proposal is removed with it rather than surviving as an offer "
            "to someone no longer in the organization. Re-fetched and "
            "refreshed under the ARCH-05 Step 1 organization lock at "
            "response time, exactly like every other owner-set mutation; "
            "this column is how Step 6 knows which row to refresh, not a "
            "cached belief about the target's current role."
        ),
    )

    status: Mapped[OwnershipTransferStatus] = mapped_column(
        PgEnum(
            OwnershipTransferStatus,
            name="ownership_transfer_status",
            create_type=False,
        ),
        default=OwnershipTransferStatus.PENDING,
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "Set once at creation (initiated_at + 7 days, §B.8) and never "
            "revised. No default at this layer, matching "
            "organization_invitations.expires_at: the service computes it "
            "explicitly so the TTL is one visible line in Step 6, not a "
            "value implied by a column definition three files away."
        ),
    )

    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "When the TARGET acted — set on the transition to ACCEPTED or "
            "to DECLINED, whichever happens. NULL while PENDING, and NULL "
            "forever for a transfer that resolves to CANCELLED or EXPIRED "
            "instead, since neither of those is something the target did. "
            "One column covering both target outcomes rather than "
            "accepted_at plus declined_at (organization_invitations' shape): "
            "status already distinguishes which one occurred, and this "
            "answers only 'when,' so a second nullable timestamp would carry "
            "no information the first doesn't already imply."
        ),
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "When the INITIATOR withdrew the proposal. Kept separate from "
            "responded_at deliberately: that column answers 'when did the "
            "target act,' and a cancellation is the one terminal state that "
            "is not a target action at all — collapsing the two would make "
            "responded_at ambiguous about who actually did something on a "
            "CANCELLED row."
        ),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    initiated_by: Mapped["User"] = relationship(
        "User",
        foreign_keys=[initiated_by_id],
    )
    target_membership: Mapped["OrganizationMember"] = relationship(
        "OrganizationMember",
        foreign_keys=[target_membership_id],
    )
