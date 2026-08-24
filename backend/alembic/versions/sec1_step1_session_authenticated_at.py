"""SEC-1 Step 1 — sessions.authenticated_at (EXPAND)

WHAT THIS COLUMN IS FOR
=======================

`auth_time`: the moment the user last actually presented a credential, as
distinct from the moment a token was minted.

ARCH-15's F6 gate reads `iat` today, and `iat` is worthless for this purpose
because ARCH-03's rotation works: every refresh mints a new access token with a
new `iat`. A session authenticated nine months ago and refreshed since carries
an `iat` a few minutes old on every request, so a 300-second re-auth window is
satisfied permanently. The window is not short — it is unreachable.

This column is the durable fact that rotation cannot launder. It is set once at
login, **copied forward unchanged** by every rotation in the family, and moved
only when the user genuinely re-authenticates.

BACKFILL
========

The family root's `created_at` — `MIN(created_at)` per `family_id`, which is
the same thing because a family is only ever extended forward by rotation.

That is the last moment we can *prove* the user authenticated. For a long-lived
rotated session it will often be months ago, and the immediate effect of this
migration is that those users are asked to re-authenticate the first time they
try to open the billing portal. That is the correct outcome and it is the whole
point of the phase.

WHY THE SERVER DEFAULT SURVIVES THE MIGRATION
=============================================

`now()` stays as a server default even though the application always supplies
the value explicitly. During a rolling deploy the old image is still running
and still inserting `sessions` rows without this column; a bare `NOT NULL`
would turn every login served by a not-yet-replaced pod into a 500 for the
length of the rollout.

The default is a deploy-window shim, not a semantic. A row that takes it is
claiming the user authenticated now, which is true for the only code path that
can reach it — `create_session` at login on the old image.

Revision ID: sec1_step1_session_authenticated_at
Revises: arch15_step8_dunning_actions
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sec1_step1_session_authenticated_at"
down_revision = "arch15_step8_dunning_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "authenticated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )

    # Backfill from the family root. A family is only ever extended forward by
    # rotation, so MIN(created_at) within a family *is* the root's created_at,
    # and it does not require walking `replaced_by_id` chains that may be
    # thousands of links long on an active account.
    op.execute(
        """
        UPDATE sessions AS us
           SET authenticated_at = roots.first_created
          FROM (
                SELECT family_id, MIN(created_at) AS first_created
                  FROM sessions
              GROUP BY family_id
               ) AS roots
         WHERE us.family_id = roots.family_id
        """
    )

    # Fallback for any row inserted between backfill and constraint
    op.execute(
        "UPDATE sessions SET authenticated_at = created_at "
        "WHERE authenticated_at IS NULL"
    )

    op.alter_column("sessions", "authenticated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("sessions", "authenticated_at")