"""
Refresh session records for FlowPilot AI.

A row is one refresh token. Access tokens are stateless JWTs and are never
recorded — checking a session row on every request would cost a query per
request for revocation that §B.6 obtains for free by comparing the token's
`iat` against `users.sessions_revoked_at`, which is already loaded.

The module is `user_session.py` and the class is `UserSession`, not
`session.py` / `Session`, deliberately. Nearly every module
that would touch it also does `from sqlalchemy.orm import Session`, and a
shadowed database session is a debugging session nobody enjoys. The module
name avoids the same collision with `app/db/session.py`, which holds the
engine and sessionmaker. The table is `sessions` as the plan specifies.

Rotation and reuse detection (§B.7) are the hardest logic in ARCH-03 and the
columns below exist to make them expressible:

    login          → row A, family F, rotated_at NULL
    refresh with A → row B, family F; A.rotated_at = now, A.replaced_by = B
    refresh with A → A is already rotated. Either a stolen token is being
                     replayed, or two tabs refreshed at once.

The grace window separates those two cases. If A was rotated within the last
10 seconds and its replacement B has not itself been rotated, the second
caller is a concurrent tab: return B rather than revoking. Otherwise revoke
every row in family F. Without the window, two tabs refreshing milliseconds
apart sign the user out of everything, which is the main operational risk of
the phase (R2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
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

class SessionRevokedReason(str, Enum):
    """
    Why a session stopped being valid.

    An enum rather than free text because the reuse-detection tests assert on
    it: "replaying a rotated token revokes the family" is only verifiable if
    REUSE_DETECTED is distinguishable from LOGOUT, and an incident review that
    cannot tell a theft from a sign-out is not a review.

    LOGOUT           the user signed out this device
    LOGOUT_ALL       the user signed out everywhere
    ROTATED          superseded by its replacement during a normal refresh
    REUSE_DETECTED   an already-rotated token was presented (§B.7)
    PASSWORD_CHANGE  password reset or change revoked all sessions (§B.6)
    EMAIL_CHANGE     the account address changed (ARCH-06 Step 6). Distinct
                     from PASSWORD_CHANGE deliberately: changing the address
                     is the step that makes an account takeover permanent,
                     and it is the event an incident review most needs to
                     find. Reusing PASSWORD_CHANGE would write a false
                     statement into every affected row.
    ACCOUNT_DISABLED administrative deactivation
    EXPIRED          closed by the sweeper past its expiry (R8)
    """
    LOGOUT = "LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    ROTATED = "ROTATED"
    REUSE_DETECTED = "REUSE_DETECTED"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    EXPIRED = "EXPIRED"
    EMAIL_CHANGE = "EMAIL_CHANGE"


# ============================================================================
# Models
# ============================================================================

class UserSession(Base, UUIDMixin, TimestampMixin):
    """
    One refresh token, and the chain it belongs to.

    Rows are retained after revocation. The device list a user sees is built
    from unrevoked rows, but the revoked ones are what make a reuse incident
    reconstructable, and deleting on rotation would destroy the chain that
    detection walks.
    """
    __tablename__ = "sessions"
    __table_args__ = (
        # The device list: this user's live sessions.
        Index(
            "ix_sessions_user_revoked",
            "user_id",
            "revoked_at",
        ),
        # Reuse detection revokes a whole family in one statement.
        Index(
            "ix_sessions_family_id",
            "family_id",
        ),
        # The sweeper (R8), which deletes past expires_at + 30 days.
        Index(
            "ix_sessions_expires_at",
            "expires_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc=(
            "Groups every session descended from one login. Set to a fresh "
            "UUID at login and copied unchanged through each rotation. "
            "Intentionally not a foreign key: it identifies a chain, not a "
            "row, and pointing it at the first session would leave the chain "
            "unidentifiable once that row is swept."
        ),
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc=(
            "SHA-256 of the refresh secret, hex encoded. Same discipline as "
            "auth_tokens: the plaintext lives only in the HttpOnly cookie."
        ),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Refresh TTL is 14 days (§B.6), applied at issuance.",
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Advanced on each successful refresh. Powers the device list.",
    )

    # --- Authentication moment (SEC-1 Step 1) ------------------------------
    authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        doc=(
            "When the user last actually presented a credential. **Copied "
            "forward by rotation, never recomputed.** "
            "\n\n"
            "This is deliberately not `created_at`. `created_at` is when this "
            "particular link in the chain was minted, which rotation refreshes "
            "every few minutes; `authenticated_at` is when a human last typed "
            "a password, which rotation must not be able to launder. The "
            "difference is the entire reason ARCH-15's F6 re-auth window was "
            "unreachable before SEC-1 — a nine-month-old session presents a "
            "`created_at` and an `iat` that are both minutes old. "
            "\n\n"
            "Moved only by a genuine credential re-presentation. If a future "
            "step-up flow updates it, it updates it on the live session rather "
            "than starting a new family, so the device list does not sprout a "
            "phantom entry every time somebody confirms a password."
        ),
    )

    # --- Rotation chain ----------------------------------------------------
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "Set when this token is exchanged for a successor. Non-NULL is "
            "the precondition for reuse detection, and the age of this value "
            "is what the 10-second grace window is measured against."
        ),
    )
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "The successor issued by rotation. SET NULL rather than CASCADE: "
            "the sweeper removing an old successor must not delete the "
            "ancestor that points at it."
        ),
    )

    # --- Revocation --------------------------------------------------------
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_reason: Mapped[SessionRevokedReason | None] = mapped_column(
        PgEnum(
            SessionRevokedReason,
            name="session_revoked_reason",
            create_type=False,
        ),
        nullable=True,
    )

    # --- Device provenance -------------------------------------------------
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        doc="Rendered as a device label in the session management screen.",
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    replaced_by: Mapped["UserSession | None"] = relationship(
        "UserSession",
        remote_side="UserSession.id",
        foreign_keys=[replaced_by_id],
    )