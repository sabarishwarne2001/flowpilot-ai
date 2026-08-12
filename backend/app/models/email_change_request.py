"""
ARCH-06 Step 2 — email change request lifecycle.

Backs §B.1/§B.2/§B.3's approved shape: a token mailed to the NEW address only
(never the old one — the old address is notified AFTER confirmation, not
asked for anything), no `previous_email` column on `users`, and a dedicated
table rather than a third `AuthTokenPurpose`.

WHY A DEDICATED TABLE, NOT A THIRD AuthTokenPurpose (§B.3)
------------------------------------------------------------
`auth_tokens` fits EMAIL_VERIFICATION and PASSWORD_RESET because both need
nothing beyond "who, which secret, until when, used or not" — the docstring on
that model states the identical-lifecycle argument for keeping them in one
table. An email change needs a THIRD piece of state neither of those does: the
destination address itself. `auth_tokens` has no column for that and adding
one would be NULL for every row except this one purpose, which is exactly the
shape a dedicated table exists to avoid. `new_email` is not incidental
metadata about the token — it is the thing the token is proving control of,
so it belongs on the row that names the request, not bolted onto a table
designed around not needing a payload.

WHY THIS MIRRORS ownership_transfers.py, LINE FOR LINE WHERE POSSIBLE
------------------------------------------------------------------------
Both tables hold exactly one live proposal per subject, enforced by the same
partial-unique-index shape; both use lazy expiry with no sweeper, for the same
reason: the person named in the row is a signed-in, verified user who will
either act or not, not a mailbox nobody is watching (`workspace_invitations`'
sweeper reasoning, ARCH-04, does not apply to either table). Reusing that
model's already-reviewed shape means this file's novelty is confined to what
is ACTUALLY different — the token and the destination address — rather than
re-deriving lifecycle plumbing that already has a working precedent.

WHY `token_hash` LOOKS LIKE `auth_tokens.token_hash`, NOT LIKE A NEW PATTERN
-------------------------------------------------------------------------------
Single-use, hashed, never plaintext, consumed by a conditional UPDATE — see
`auth_token.py`'s module docstring for the concurrency argument, which applies
here without modification:

    UPDATE email_change_requests SET status = 'COMPLETED', consumed_at = now()
     WHERE token_hash = :hash AND status = 'PENDING' AND expires_at > now()
     RETURNING *

`String(255)`, not `auth_tokens.token_hash`'s `String(64)`. A SHA-256 hex
digest is always exactly 64 characters, so 255 is not sized for the current
algorithm — it is sized so a future change of hash algorithm (a longer digest,
a different encoding) is a value change, not a schema migration. Widening a
VARCHAR is a metadata-only operation in PostgreSQL; narrowing it, or the
`auth_tokens` table's tighter bound, would not be if that day comes. `unique`
and `index` are unchanged from the `auth_tokens` precedent: this is still a
single, globally-unique lookup key, and the uniqueness is what makes a hash
collision or an accidental double-issue impossible to persist, not merely
unlikely.

WHY `email_change_requests.user_id` HAS NO UNIQUE CONSTRAINT OF ITS OWN
----------------------------------------------------------------------------
`uq_pending_email_change_per_user` below is a PARTIAL index —
`WHERE status = 'PENDING'`. A plain unique constraint on `user_id` would
permit only one row per user EVER, which would make a second, later email
change impossible once the first ever completed. The partial index is what
makes "one LIVE request" enforceable while leaving history intact, exactly
mirroring `uq_pending_ownership_transfer_per_org`.

STEP 2 IS EXPAND ONLY
----------------------
This table is new and starts empty — there is no existing data to interpret
into it, so every constraint here applies from row zero, same as
`ownership_transfers`. Step 6 is what writes to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


# ============================================================================
# Enumerations
# ============================================================================

class EmailChangeStatus(str, PyEnum):
    """
    Lifecycle of a proposed email change.

    Backs the PostgreSQL type `email_change_status`, created fresh by the
    Step 2 EXPAND migration — there is no prior table this reuses, matching
    `ownership_transfer_status`'s situation rather than the pre-existing enum
    types ARCH-01 aligned.

    Permitted transitions:

        PENDING ──► COMPLETED     (terminal — confirm_email_change ran)
            │
            ├────► CANCELLED      (terminal — withdrawn by the requester)
            └────► EXPIRED        (terminal)

    COMPLETED, not ACCEPTED: unlike `ownership_transfers`, there is no second
    party whose agreement is being recorded. The requester and the prover of
    the new address are the same actor — Step 6's `confirm_email_change` is
    the requester finishing what they started, not someone else responding to
    an offer. "Accepted" would misname whose action this is.

    EXPIRED is written lazily, exactly as `OwnershipTransferStatus.EXPIRED`
    is and for the identical reason stated on that enum: the subject of this
    row is a signed-in, already-verified user, not a mailbox nobody is
    watching, so there is no unattended-inbox problem for a sweeper to
    reconcile and no third cron entry earning its keep. A row can sit at
    `status = PENDING` with `expires_at` in the past until something touches
    it; `confirm_email_change` or `request_email_change` (on a fresh request)
    writes `EXPIRED` at the moment either discovers it, not on a schedule.
    Never derive "is this expired" from `status` alone — compare `expires_at`
    to the current time, as `claim_invitation` and `TransferExpiredError`'s
    call sites both already do.
    """

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# ============================================================================
# Models
# ============================================================================

class EmailChangeRequest(Base, UUIDMixin, TimestampMixin):
    """
    A proved-in-progress change of a user's email address.

    Existence of a PENDING row says nothing about what `users.email` currently
    is — that column is the only source of truth for the user's actual address
    at any moment, exactly as `Organization`'s membership rows are the only
    source of truth for current ownership (see `ownership_transfer.py`). This
    table is the record of an attempt and its resolution, nothing more.
    `email_change_service.confirm_email_change` (Step 6) is the only code path
    that writes `users.email` outside registration — see `EmailImmutableError`
    on the primary-enforcement split between the schema layer and that
    exception.

    Rows are retained after every terminal state, not deleted. A COMPLETED row
    is the evidence a change actually happened and when; a CANCELLED or
    EXPIRED row is what a support conversation needs when a user reports "I
    tried to change my email and it didn't work." `auth_tokens` makes the same
    choice for the same reason.
    """

    __tablename__ = "email_change_requests"
    __table_args__ = (
        # §B.3/§B.9 (ARCH-06 Section C Step 2). One LIVE request per user — a
        # partial index, not a plain UNIQUE(user_id), so history survives past
        # the first completed or abandoned attempt. Directly the
        # uq_pending_ownership_transfer_per_org pattern: the literal string is
        # the enum member's own value, matching that index's convention.
        Index(
            "uq_pending_email_change_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Serves confirm_email_change's "does this user have anything else
        # pending" check and any future "my email change history" read,
        # without a sequential scan as the table grows. Anticipates the same
        # kind of list view ix_ownership_transfers_organization_status
        # anticipated for that table, for the identical reason: the partial
        # index above cannot serve a query that needs COMPLETED, CANCELLED,
        # or EXPIRED rows.
        Index(
            "ix_email_change_requests_user_status",
            "user_id",
            "status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "The account requesting the change. CASCADE: a request detached "
            "from its user proves control of an address on behalf of nobody "
            "and is not evidence of anything on its own — matches "
            "auth_tokens.user_id's identical reasoning."
        ),
    )

    new_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc=(
            "The address being proved, NOT yet written to users.email — that "
            "write is confirm_email_change's alone, and happens only after "
            "this address's control is proved via token_hash. Same length as "
            "users.email (String(255)), since this value becomes that column "
            "verbatim on success. Deliberately NOT unique at the database "
            "layer: two different users may each have a PENDING request "
            "naming the same address (one typo, one legitimate), and only "
            "confirm_email_change's re-check immediately before the write "
            "(§C Step 6, ordering step 2) decides which — if either — may "
            "actually claim it. A DB-level uniqueness constraint here would "
            "reject the second REQUEST outright, when the correct moment to "
            "resolve the race is at CONFIRMATION, against users.email's own "
            "unique index, not here."
        ),
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        doc=(
            "SHA-256 of secrets.token_urlsafe(32), hex encoded, matching "
            "auth_tokens.token_hash's algorithm and the reasoning on that "
            "column for why this is not bcrypt: a 256-bit random secret is "
            "not guessable, so a slow KDF buys nothing here either. Sized "
            "String(255) rather than auth_tokens' String(64) — see the "
            "module docstring — to leave room for a future algorithm change "
            "without a migration; the value written today is still exactly "
            "64 hex characters."
        ),
    )

    status: Mapped[EmailChangeStatus] = mapped_column(
        PgEnum(
            EmailChangeStatus,
            name="email_change_status",
            create_type=False,
        ),
        default=EmailChangeStatus.PENDING,
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "Set once at creation and never revised, matching "
            "ownership_transfers.expires_at and organization_invitations."
            "expires_at: computed explicitly by the service so the TTL is "
            "one visible line in Step 6, not a value implied by a column "
            "definition three files away. No default at this layer."
        ),
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "When confirm_email_change's conditional UPDATE matched this "
            "row. NULL while PENDING, and NULL forever for a row that "
            "resolves to CANCELLED or EXPIRED instead — those are not a "
            "consumption of the token, they are the token becoming "
            "unusable without ever having been used. Mirrors "
            "auth_tokens.consumed_at precisely; deliberately NOT reused as "
            "a stand-in for CANCELLED or EXPIRED, for the same reason "
            "ownership_transfers keeps responded_at and cancelled_at apart: "
            "status already distinguishes which terminal state was reached, "
            "and this column answers only 'when the token was spent,' which "
            "a cancellation or expiry is not."
        ),
    )

    # ------------------------------------------------------------------
    # Unidirectional relationship (ARCH-02 discipline)
    # ------------------------------------------------------------------
    # No corresponding collection on User. A back reference would let
    # `user.email_change_requests` load unbounded history by default on every
    # access to a User instance — auth_token.py's identical note applies
    # verbatim, and this table has the same shape of risk.
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )