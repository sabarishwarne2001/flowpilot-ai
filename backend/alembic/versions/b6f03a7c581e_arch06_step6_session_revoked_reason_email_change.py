"""arch06_step6_session_revoked_reason_email_change

Revision ID: b6f03a7c581e
Revises: e1c9a7f42d63
Create Date: 2026-08-17 09:00:00.000000

ARCH-06 Step 6 — adds EMAIL_CHANGE to the session_revoked_reason enum.

WHY THIS MIGRATION EXISTS AT ALL, WHEN STEP 6 WAS SCOPED AS SERVICE-ONLY
---------------------------------------------------------------------------
`confirm_email_change` must revoke every session (§C Step 6, ordering step
5), and `session_service.revoke_all_user_sessions` requires a
`SessionRevokedReason`. The enum's existing members are:

    LOGOUT  LOGOUT_ALL  ROTATED  REUSE_DETECTED  PASSWORD_CHANGE
    ACCOUNT_DISABLED  EXPIRED

None of them describes an email change. The nearest, PASSWORD_CHANGE, is
simply false — no password changed — and reusing it would write that false
statement into every affected session row permanently.

The enum's own docstring is explicit that this matters:

    "An enum rather than free text because the reuse-detection tests assert
    on it: 'replaying a rotated token revokes the family' is only verifiable
    if REUSE_DETECTED is distinguishable from LOGOUT, and an incident review
    that cannot tell a theft from a sign-out is not a review."

An incident review that cannot tell an email change from a password change
is the same defect. Account-takeover investigations turn on exactly this
distinction: changing the address is the step that makes a takeover
permanent, and it is the event a reviewer most needs to find. Adding the
value is a two-line migration; the alternative is a permanent, unfixable
ambiguity in the one table an incident review reads first.

WHY autocommit_block()
-------------------------
`ALTER TYPE ... ADD VALUE` cannot be used by a statement in the SAME
transaction that added it (PostgreSQL restriction, still present in 16).
Alembic runs `upgrade()` in one transaction by default, so the ADD VALUE
must run outside it or any later statement referencing EMAIL_CHANGE in this
same migration would fail. `op.get_context().autocommit_block()` is
Alembic's supported mechanism for exactly this.

The cost is that this one statement is not atomic with the rest of the
revision. That is acceptable here specifically because ADD VALUE is itself
additive and idempotent-guarded below: a partial application leaves an enum
with an extra permitted value and nothing referencing it, which is inert.
Contrast `84251cd213bd`'s reasoning for refusing autocommit elsewhere —
there the non-atomic operations were data-bearing, and losing atomicity
would have risked a half-migrated table. An unused enum member risks
nothing.

WHY downgrade() DOES NOT REMOVE THE VALUE
--------------------------------------------
PostgreSQL has no `ALTER TYPE ... DROP VALUE`. Removing an enum member
requires creating a replacement type, rewriting every column that uses it,
and dropping the old one — an operation that would fail outright if any row
still carries the value being removed, which after a single email change it
would. `downgrade()` is therefore deliberately a no-op with a logged
explanation rather than a broken best-effort. This is a genuine
irreversibility, stated rather than hidden: downgrading past this revision
leaves the extra enum value in place, harmlessly, and re-upgrading is a
no-op because of the guard below.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b6f03a7c581e"
down_revision: Union[str, None] = "e1c9a7f42d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.arch06.step6")

ENUM_TYPE = "session_revoked_reason"
NEW_VALUE = "EMAIL_CHANGE"


def _value_exists(conn) -> bool:
    return conn.execute(
        sa.text("""
            SELECT 1
              FROM pg_enum e
              JOIN pg_type t ON t.oid = e.enumtypid
             WHERE t.typname = :type_name
               AND e.enumlabel = :label
        """),
        {"type_name": ENUM_TYPE, "label": NEW_VALUE},
    ).scalar() is not None


def upgrade() -> None:
    # Guarded rather than relying on IF NOT EXISTS alone, so the log line
    # distinguishes "added" from "already present" — the same discipline
    # a4d17c9e2b58 (Step 1a) applies to its renames.
    if _value_exists(op.get_bind()):
        logger.info(
            "ARCH06_STEP6 | ALREADY_PRESENT | %s.%s", ENUM_TYPE, NEW_VALUE
        )
        return

    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {ENUM_TYPE} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'"
        )

    logger.info("ARCH06_STEP6 | ADDED | %s.%s", ENUM_TYPE, NEW_VALUE)


def downgrade() -> None:
    """
    Deliberately a no-op. See the module docstring: PostgreSQL cannot drop an
    enum value, and the type-swap alternative would fail against any row that
    already carries it.
    """
    logger.warning(
        "ARCH06_STEP6 | DOWNGRADE_NOOP | '%s' remains a permitted value of "
        "%s. PostgreSQL cannot drop an enum value; removing it would require "
        "recreating the type and rewriting every column using it, which fails "
        "outright if any session row already carries it. The extra value is "
        "inert once nothing references it.",
        NEW_VALUE, ENUM_TYPE,
    )
