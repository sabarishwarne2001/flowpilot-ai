"""ARCH-03 CONTRACT — enforce hashed invitation tokens, drop plaintext

The one-way door of this phase. Everything before it was additive or
reversible; this revision destroys data that cannot be reconstructed, and the
downgrade below can restore the shape of the schema but not its contents.

Take a dump before running it.

WHAT IS ENFORCED AND WHAT DELIBERATELY IS NOT
---------------------------------------------
Enforced:
  - workspace_invitations.token_hash        NOT NULL, UNIQUE
  - workspace_invitations.token             DROPPED

Deliberately NOT enforced:
  - users.email_verified_at stays NULLABLE, permanently. The plan's Step 5 text
    called for NOT NULL here and was wrong: NULL is the representation of an
    unverified account, so the constraint would make registration in an
    unverified state impossible and contradict Step 8. Step 4's "zero NULL"
    assertion was about the four rows that existed at that moment, not about
    the column's domain.
  - sessions and auth_tokens receive nothing here. Both were created in final
    form by EXPAND, indexes included, because they were new and empty and there
    was nothing to slow down. The composite indexes the plan scheduled for this
    revision already exist.

THE work_items CONVERGENCE
--------------------------
c6f653bef578 created work_items with an unnamed sa.UniqueConstraint on
stored_filename. What Postgres named it depends on whether the metadata naming
convention was in force at the time:

  - Databases built before NAMING_CONVENTION existed got Postgres's default,
    work_items_stored_filename_key. 6045810b083a looked for exactly that name,
    found it, and dropped it.
  - Databases built by running the chain afterwards get
    uq_work_items_stored_filename from the convention. The same guard finds
    nothing, its drop is a silent no-op, and the constraint survives alongside
    the unique index that replaced it.

The result is a chain that no longer reproduces: a database built from
migrations today does not match one built earlier, so CI and production
disagree on schema. DROP CONSTRAINT IF EXISTS converges them — a no-op where
the guard worked, corrective where it did not. Uniqueness is unaffected either
way: ix_work_items_stored_filename is a UNIQUE index and remains the enforcing
object, which is also what 84251cd213bd's own survival assertion checks.

Revision ID: c3f8a6b21d47
Revises: b7d4e0f81a63
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a6b21d47"
down_revision: Union[str, None] = "b7d4e0f81a63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


logger = logging.getLogger("alembic.arch03.contract")

SHA256_HEX_LENGTH = 64


def _assert_safe_to_drop_plaintext(bind: sa.engine.Connection) -> None:
    """
    Re-verifies MIGRATE's work immediately before the column is destroyed.

    Step 4 already asserted all of this and its own gate script confirmed it.
    It is asserted again here because the interval between the two revisions is
    unbounded, the plaintext is about to become unrecoverable, and the cost of
    being wrong is every outstanding invitation becoming permanently
    unacceptable with no way to reissue the original links.
    """
    total = bind.execute(
        sa.text("SELECT count(*) FROM workspace_invitations")
    ).scalar_one()

    null_hash = bind.execute(
        sa.text(
            "SELECT count(*) FROM workspace_invitations WHERE token_hash IS NULL"
        )
    ).scalar_one()
    if null_hash:
        raise RuntimeError(
            f"ARCH-03 CONTRACT: {null_hash} of {total} invitations have a NULL "
            "token_hash. Re-run MIGRATE, or find out what created rows after "
            "it ran. Nothing has been dropped."
        )

    bad_length = bind.execute(
        sa.text(
            "SELECT count(*) FROM workspace_invitations "
            "WHERE length(token_hash) <> :expected"
        ),
        {"expected": SHA256_HEX_LENGTH},
    ).scalar_one()
    if bad_length:
        raise RuntimeError(
            f"ARCH-03 CONTRACT: {bad_length} token_hash values are not "
            f"{SHA256_HEX_LENGTH} characters."
        )

    duplicates = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM (
                SELECT token_hash FROM workspace_invitations
                 GROUP BY token_hash HAVING count(*) > 1
            ) d
            """
        )
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            f"ARCH-03 CONTRACT: {duplicates} duplicate token_hash values. "
            "The UNIQUE index below would fail."
        )

    # The check that actually licenses the drop: every stored hash must still
    # correspond to the plaintext about to be destroyed. A hash that does not
    # reproduce means MIGRATE wrote it from something else, and dropping the
    # column would leave an invitation nobody can accept.
    #
    # IS DISTINCT FROM, and an explicit NULL test, both matter here. A plain
    # <> comparison yields NULL when token is NULL, count(*) over a NULL
    # predicate is zero, and the check passes silently on exactly the rows it
    # exists to catch. That is reachable: downgrade() recreates token as an
    # all-NULL column, so the rehearsal path downgrade -> upgrade would
    # otherwise drop the column a second time while reporting everything fine.
    inconsistent = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM workspace_invitations
             WHERE token IS NULL
                OR token_hash IS DISTINCT FROM encode(sha256(token::bytea), 'hex')
            """
        )
    ).scalar_one()
    if inconsistent:
        raise RuntimeError(
            f"ARCH-03 CONTRACT: {inconsistent} rows have a NULL plaintext "
            "token or a stored hash that does not reproduce from it. Dropping "
            "the column would strand those invitations permanently. Nothing "
            "has been dropped. If this follows a downgrade, the plaintext is "
            "already gone — restore from a pre-CONTRACT dump."
        )

    logger.info(
        "ARCH-03 CONTRACT pre-flight OK — %d invitations, every hash present, "
        "distinct, and consistent with its plaintext.",
        total,
    )


def upgrade() -> None:
    bind = op.get_bind()

    _assert_safe_to_drop_plaintext(bind)

    # -----------------------------------------------------------------------
    # 1. workspace_invitations.token_hash — UNIQUE then NOT NULL
    #
    # Unique index first. If a duplicate somehow survived the pre-flight, the
    # failure names the offending value; a NOT NULL that succeeded first would
    # leave the table half-constrained inside a rolled-back transaction and
    # give a less useful error.
    # -----------------------------------------------------------------------
    op.create_index(
        "ix_workspace_invitations_token_hash",
        "workspace_invitations",
        ["token_hash"],
        unique=True,
    )
    op.alter_column(
        "workspace_invitations",
        "token_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    logger.info("ARCH-03 CONTRACT token_hash is now UNIQUE NOT NULL.")

    # -----------------------------------------------------------------------
    # 2. Drop the plaintext column
    #
    # Its index goes first and explicitly. Postgres would drop it with the
    # column, but naming it here means the downgrade has an exact inverse to
    # write rather than an implicit one to remember.
    #
    # From this point a read of workspace_invitations yields nothing that
    # grants membership to anything (§A.2.2 closed).
    # -----------------------------------------------------------------------
    op.drop_index(
        "ix_workspace_invitations_token",
        table_name="workspace_invitations",
    )
    op.drop_column("workspace_invitations", "token")
    logger.info(
        "ARCH-03 CONTRACT dropped workspace_invitations.token — no invitation "
        "secret is stored in plaintext anywhere."
    )

    # -----------------------------------------------------------------------
    # 3. work_items convergence — see the module docstring
    # -----------------------------------------------------------------------
    op.execute(
        sa.text(
            "ALTER TABLE work_items "
            "DROP CONSTRAINT IF EXISTS uq_work_items_stored_filename"
        )
    )
    logger.info(
        "ARCH-03 CONTRACT work_items unique-constraint convergence applied "
        "(no-op where 6045810b083a already succeeded)."
    )

    # -----------------------------------------------------------------------
    # 4. users.email_verified_at is NOT constrained. See the module docstring.
    # -----------------------------------------------------------------------
    unverified = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE email_verified_at IS NULL")
    ).scalar_one()
    logger.info(
        "ARCH-03 CONTRACT users.email_verified_at left nullable by design; "
        "%d users currently unverified.",
        unverified,
    )

    logger.info("ARCH-03 CONTRACT complete — schema now matches the models.")


def downgrade() -> None:
    bind = op.get_bind()

    # =======================================================================
    # THIS DOWNGRADE RESTORES SHAPE, NOT DATA.
    #
    # The plaintext tokens are gone. They were the only copy the server held,
    # and no derivation recovers them from a SHA-256 digest. The column is
    # recreated NULLABLE because it cannot be recreated NOT NULL over rows
    # that have no value to put in it.
    #
    # If you need the invitations to work again, restore from a dump taken
    # before this revision. That is the rollback path; this function exists so
    # the revision is replayable and so an operator who runs it by reflex gets
    # a working schema and an unmissable warning rather than a broken one.
    # =======================================================================
    op.add_column(
        "workspace_invitations",
        sa.Column("token", sa.String(length=512), nullable=True),
    )
    # Unique, as it was. Postgres treats NULLs as distinct, so a column of
    # NULLs satisfies it — which is precisely why this is not a restoration.
    op.create_index(
        "ix_workspace_invitations_token",
        "workspace_invitations",
        ["token"],
        unique=True,
    )

    op.alter_column(
        "workspace_invitations",
        "token_hash",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.drop_index(
        "ix_workspace_invitations_token_hash",
        table_name="workspace_invitations",
    )

    stranded = bind.execute(
        sa.text("SELECT count(*) FROM workspace_invitations WHERE token IS NULL")
    ).scalar_one()

    logger.warning(
        "ARCH-03 CONTRACT reversed — schema only. %d invitations now have a "
        "NULL plaintext token and CANNOT be accepted through any link. The "
        "plaintext was destroyed by upgrade() and is not recoverable. Restore "
        "from a pre-CONTRACT dump if these invitations must work again.",
        stranded,
    )

    # The work_items convergence is intentionally not reversed. Recreating a
    # redundant unique constraint would reintroduce the divergence this
    # revision fixed, and no application behaviour depends on it existing.
    logger.info(
        "ARCH-03 CONTRACT work_items convergence deliberately not reversed."
    )
