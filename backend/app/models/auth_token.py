"""
Single-use authentication tokens for FlowPilot AI.

One table serves email verification and password reset because both have an
identical lifecycle: a high-entropy secret is issued, stored only as a hash,
consumed exactly once, and expires whether or not it is used. Splitting them
per purpose would duplicate the consumption routine, and consumption is where
the single-use guarantee lives — duplicating it means two places to get the
security property wrong. Purpose-specific rules (TTL, rate limit, what
consumption does) live in the service layer, keyed on `purpose`. ARCH-03 §B.2.

The plaintext token is never stored. It exists in exactly two places: the URL
in the recipient's mailbox, and the request body when they submit it. There is
no column for it, so a read of this table yields nothing an attacker can use.

Consumption must be a conditional UPDATE, never a read-then-write:

    UPDATE auth_tokens SET consumed_at = now()
     WHERE token_hash = :hash
       AND consumed_at IS NULL
       AND invalidated_at IS NULL
       AND expires_at > now()

A SELECT followed by an UPDATE has a window in which two concurrent requests
both observe an unconsumed row and both proceed. The WHERE clause closes it:
the second UPDATE matches zero rows and the caller sees the token as spent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


# ============================================================================
# Enumerations
# ============================================================================

class AuthTokenPurpose(str, Enum):
    """
    What a token authorizes when it is consumed.

    Invitation tokens are deliberately absent. They live in
    workspace_invitations, which carries workspace, role, and inviter columns
    this table has no place for, and ARCH-04 extends that lifecycle further.
    They adopt the same hashing discipline in Steps 3–5 without moving here.

    Postgres accepts new enum values via ALTER TYPE ... ADD VALUE, so a third
    purpose later is a one-line migration. Adding a value now that nothing
    issues would be a value that cannot be removed without a type rewrite.
    """
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"


# ============================================================================
# Models
# ============================================================================

class AuthToken(Base, UUIDMixin, TimestampMixin):
    """
    A single-use secret issued to a user for one identity operation.

    Rows are retained after consumption rather than deleted. A consumed token
    is the evidence that a verification or reset actually completed, and the
    difference between "this token was already used" and "this token never
    existed" matters when a user reports that a reset link did not work. The
    sweeper removes rows well past expiry (ARCH-03 R8 applies here too).
    """
    __tablename__ = "auth_tokens"
    __table_args__ = (
        # Serves the two queries the service actually issues: every
        # outstanding token of one purpose for one user, which is what
        # invalidate_all_for_purpose needs on a successful reset, and the
        # per-user issuance rate limit. Consumption looks up by token_hash and
        # uses that column's unique index instead.
        Index(
            "ix_auth_tokens_user_purpose_consumed",
            "user_id",
            "purpose",
            "consumed_at",
        ),
        # Supports the sweeper without a sequential scan as the table grows.
        Index(
            "ix_auth_tokens_expires_at",
            "expires_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        # Covered as the leading column of ix_auth_tokens_user_purpose_consumed.
        doc=(
            "Owner of the token. CASCADE rather than SET NULL: a token "
            "detached from its user authorizes an operation on nobody, and "
            "keeping it is a live secret with no subject."
        ),
    )
    purpose: Mapped[AuthTokenPurpose] = mapped_column(
        PgEnum(
            AuthTokenPurpose,
            name="auth_token_purpose",
            create_type=False,
        ),
        nullable=False,
        doc=(
            "Checked on consumption. Without it a verification token would "
            "reset a password, since both are 256-bit secrets in one table."
        ),
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc=(
            "SHA-256 of secrets.token_urlsafe(32), hex encoded — always 64 "
            "characters. Not bcrypt: the input is a 256-bit random secret, "
            "not a password, so it is not guessable and a slow KDF buys "
            "nothing while costing latency on every verification click."
        ),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # --- Terminal states ---------------------------------------------------
    # Consumed and invalidated are distinct and both are checked on
    # consumption. Consumed means the user used it. Invalidated means the
    # system withdrew it — a successful password reset invalidates every other
    # outstanding reset token for that user, so a link mailed to a stolen
    # inbox stops working the moment the real owner completes a reset.
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc=(
            "Free text rather than an enum: this is read by a human during "
            "an incident, never branched on in code."
        ),
    )

    # --- Issuance provenance -----------------------------------------------
    # Recorded at issuance, not at consumption. If a user reports a reset they
    # did not request, this is the only record of where the request came from.
    requested_ip: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        doc="45 characters covers the longest IPv4-mapped IPv6 form.",
    )
    requested_user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Unidirectional relationships (ARCH-02 discipline)
    # ------------------------------------------------------------------
    # No corresponding collection on User. A back reference would let
    # `user.auth_tokens` load every token a user has ever been issued, which
    # is both unbounded and the exact object a debugger is most likely to
    # print into a log.
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )