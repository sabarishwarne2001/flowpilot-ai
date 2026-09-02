"""ARCH-03 EXPAND — identity lifecycle tables and columns

Purely additive. Two new tables, two new enum types, three new columns, and no
row is written or altered. The pre-ARCH-03 application runs unchanged against
the resulting schema, and downgrade() returns the database to a byte-identical
starting point because nothing here destroys information.

Constraint policy, following ARCH-02:

  - The two NEW tables are created in final form: NOT NULL, unique indexes,
    composite indexes, and foreign keys all applied immediately. They are
    empty and nothing is about to bulk-write them, so there is nothing to slow
    down and no reason to schedule a second lock later.

  - The three columns added to EXISTING tables are nullable with no server
    default, and their NOT NULL / UNIQUE constraints are deferred to CONTRACT.
    workspace_invitations.token_hash in particular is about to be bulk-written
    by MIGRATE; a unique index built now would only be maintained through that
    UPDATE and then verified again at CONTRACT.

  - users.email_verified_at is deferred to CONTRACT for its backfill only. It
    is NOT made NOT NULL there, or ever: NULL is the representation of an
    unverified account and is a permanent, meaningful value (§B.4). The plan's
    Step 5 text said otherwise and was wrong.

Enum types are created explicitly at the top of upgrade(), and every column
reference below uses create_type=False. Without that flag SQLAlchemy emits a
CREATE TYPE alongside each column that uses the type, and the second emission
fails with "type already exists" — the same pattern ARCH-01 established in
4fb2e9a4f15c.

Revision ID: a1c7f39b4e2d
Revises: 84251cd213bd
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1c7f39b4e2d"
down_revision: Union[str, None] = "84251cd213bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ===========================================================================
# Enum type definitions
# ===========================================================================

AUTH_TOKEN_PURPOSE = postgresql.ENUM(
    "EMAIL_VERIFICATION",
    "PASSWORD_RESET",
    name="auth_token_purpose",
    create_type=False,
)

SESSION_REVOKED_REASON = postgresql.ENUM(
    "LOGOUT",
    "LOGOUT_ALL",
    "ROTATED",
    "REUSE_DETECTED",
    "PASSWORD_CHANGE",
    "ACCOUNT_DISABLED",
    "EXPIRED",
    name="session_revoked_reason",
    create_type=False,
)

_NEW_ENUM_TYPES = (
    AUTH_TOKEN_PURPOSE,
    SESSION_REVOKED_REASON,
)


def upgrade() -> None:
    bind = op.get_bind()

    # =======================================================================
    # 1. Enum types
    # =======================================================================
    for enum_type in _NEW_ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    # =======================================================================
    # 2. auth_tokens — single-use identity secrets (§B.2)
    #
    # New and empty, so full constraints apply immediately.
    #
    # There is deliberately no plaintext column. The single-use guarantee is
    # enforced by the WHERE clause of the consumption UPDATE rather than by
    # any constraint here; what this table contributes is the unique index on
    # token_hash, which makes a hash collision or a double-issue impossible to
    # persist.
    # =======================================================================
    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", AUTH_TOKEN_PURPOSE, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(length=100), nullable=True),
        sa.Column("requested_ip", sa.String(length=45), nullable=True),
        sa.Column("requested_user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_tokens_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_auth_tokens_token_hash",
        "auth_tokens",
        ["token_hash"],
        unique=True,
    )
    # Serves invalidate_all_for_purpose and the per-user issuance rate limit.
    op.create_index(
        "ix_auth_tokens_user_purpose_consumed",
        "auth_tokens",
        ["user_id", "purpose", "consumed_at"],
        unique=False,
    )
    # Sweeper support (R8 applies to this table as well as sessions).
    op.create_index(
        "ix_auth_tokens_expires_at",
        "auth_tokens",
        ["expires_at"],
        unique=False,
    )

    # =======================================================================
    # 3. sessions — refresh token records and rotation chains (§B.7)
    #
    # replaced_by_id is a self-referential FK created inline. It is ON DELETE
    # SET NULL rather than CASCADE: the sweeper removing an expired successor
    # must not take its ancestor with it, or a reuse investigation loses the
    # chain it was about to walk.
    #
    # family_id is intentionally NOT a foreign key. It names a chain, not a
    # row; pointing it at the first session would leave the chain
    # unidentifiable the moment that row is swept.
    # =======================================================================
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", SESSION_REVOKED_REASON, nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["sessions.id"],
            name="fk_sessions_replaced_by_id_sessions",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_sessions_token_hash",
        "sessions",
        ["token_hash"],
        unique=True,
    )
    # The device list: this user's live sessions.
    op.create_index(
        "ix_sessions_user_revoked",
        "sessions",
        ["user_id", "revoked_at"],
        unique=False,
    )
    # Reuse detection revokes an entire family in one statement.
    op.create_index(
        "ix_sessions_family_id",
        "sessions",
        ["family_id"],
        unique=False,
    )
    # Sweeper support (R8).
    op.create_index(
        "ix_sessions_expires_at",
        "sessions",
        ["expires_at"],
        unique=False,
    )

    # =======================================================================
    # 4. users — verification state and the global session cutoff
    #
    # Both nullable, no server default. A server default on
    # email_verified_at would silently mark every future registration as
    # verified, which is the one outcome this phase exists to prevent; the
    # backfill of existing rows is MIGRATE's job and is scoped to rows that
    # exist at that moment.
    # =======================================================================
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("sessions_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # =======================================================================
    # 5. workspace_invitations — hashed token column
    #
    # Nullable here, backfilled by MIGRATE, made NOT NULL and UNIQUE by
    # CONTRACT, at which point the plaintext token column is dropped.
    #
    # From this revision onward the invitation service MUST write both token
    # and token_hash on every create. An invitation issued between MIGRATE and
    # CONTRACT with a NULL hash will fail CONTRACT's NOT NULL.
    # =======================================================================
    op.add_column(
        "workspace_invitations",
        sa.Column("token_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse order. Nothing here loses data that this revision did not
    # itself create: the three added columns are all-NULL until MIGRATE runs,
    # and the two tables did not exist before.
    op.drop_column("workspace_invitations", "token_hash")

    op.drop_column("users", "sessions_revoked_at")
    op.drop_column("users", "email_verified_at")

    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_family_id", table_name="sessions")
    op.drop_index("ix_sessions_user_revoked", table_name="sessions")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_auth_tokens_expires_at", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_purpose_consumed", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_token_hash", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    # Types last: dropping a type still referenced by a column would fail, so
    # this must follow the tables that use them.
    for enum_type in reversed(_NEW_ENUM_TYPES):
        enum_type.drop(bind, checkfirst=True)
