"""ARCH-06 Step 1a — rename the two double-prefixed check constraints

Fixes A.2.6. Two ARCH-04 constraints carry their prefix twice:

    organizations             ck_organizations_ck_organizations_seat_limit_positive
    organization_invitations  ck_organization_invitations_ck_organization_invitations_dd77

The models declare the SHORT forms (`name="seat_limit_positive"` on
`Organization`, `name="role_not_owner"` on `OrganizationInvitation`). The
metadata naming convention in `app/db/base.py` is:

    "ck": "ck_%(table_name)s_%(constraint_name)s"

so the names SQLAlchemy renders — and therefore the names every future
migration, every DROP CONSTRAINT, and every `\d` inspection will use — are:

    ck_organizations_seat_limit_positive
    ck_organization_invitations_role_not_owner

The ARCH-04 migration passed already-qualified names into that convention,
which re-wrapped them. Today `ALTER TABLE organizations DROP CONSTRAINT
ck_organizations_seat_limit_positive` fails with "constraint does not exist",
against a database where the constraint plainly does exist.

WHY THIS WAS NOT CAUGHT BY AUTOGENERATE, AND WILL NOT BE
--------------------------------------------------------
Alembic does not render check-constraint drift as migration operations at all.
It compares tables, columns, indexes, unique constraints, and foreign keys.
Check constraints are not in the comparison set. ARCH-05's clean autogenerate
was therefore not evidence of anything about these two names, and the ARCH-06
E16 gate ("autogenerate body is `pass`") will stay clean whether or not this
revision runs. The only detection is the direct pg_constraint query in
`scripts/verify_arch06_step0.py` (A.1.6). That is why this migration asserts
its own postcondition below rather than trusting a later autogenerate to
notice.

WHY RENAME RATHER THAN DROP AND RECREATE
-----------------------------------------
`ALTER TABLE ... RENAME CONSTRAINT` is a catalog-only operation. It takes an
ACCESS EXCLUSIVE lock for the duration of a catalog row update and does no
table scan.

Drop-and-recreate would instead:

  1. leave a window — however short — in which the invariant is UNENFORCED,
     during which a concurrent writer could commit a row the constraint
     exists to forbid, after which the recreate itself would fail; and
  2. force a full validation scan of both tables on the recreate.

Neither cost is acceptable for what is purely a naming correction. The
constraint expressions are not touched by this revision and must not be.

IDEMPOTENCE
-----------
Both directions are guarded by a pg_constraint lookup rather than issuing a
bare RENAME. Three states are possible per constraint and all three are
handled explicitly:

  - malformed name present  -> rename it
  - correct name present    -> already done; skip and log
  - neither present         -> RAISE

The third case is deliberately fatal. A missing check constraint means the
database does not match the model, and silently continuing would let a
migration chain complete against a schema that has lost an invariant. Failing
here surfaces that at deploy time instead of at the first bad INSERT.

Note that a fresh database built by replaying the chain from base REPRODUCES
the malformed names, because the ARCH-04 revision that created them is still
in the chain and is not rewritten by this one. So this revision does real work
on both an existing database and a from-scratch build — which matters, because
`tests/conftest.py` builds the test database with `alembic upgrade head`.

Revision ID: a4d17c9e2b58
Revises: 3cdea80e19f3
Create Date: 2026-08-12

"""
from __future__ import annotations
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4d17c9e2b58"
down_revision: Union[str, None] = "3cdea80e19f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


logger = logging.getLogger("alembic.arch06.step1a")


#: (table, malformed name as it exists today, correct name per the convention)
#:
#: Lengths are checked below against PostgreSQL's 63-character identifier
#: limit. ARCH-05 Step 3 found this plan's own arithmetic wrong by two
#: characters once already; the assertion is cheaper than repeating that.
#:
#:   ck_organizations_seat_limit_positive        = 36
#:   ck_organization_invitations_role_not_owner  = 42
_RENAMES: tuple[tuple[str, str, str], ...] = (
    (
        "organizations",
        "ck_organizations_ck_organizations_seat_limit_positive",
        "ck_organizations_seat_limit_positive",
    ),
    (
        "organization_invitations",
        "ck_organization_invitations_ck_organization_invitations_dd77",
        "ck_organization_invitations_role_not_owner",
    ),
)

_MAX_IDENTIFIER_LENGTH = 63


def _constraint_exists(table: str, name: str) -> bool:
    """
    Returns True when a CHECK constraint of this name exists on this table.

    Filtered by `contype = 'c'` and by relation, so a same-named constraint on
    an unrelated table cannot produce a false positive.
    """
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
              FROM pg_constraint
             WHERE contype = 'c'
               AND conrelid = CAST(:table AS regclass)
               AND conname = :name
            """
        ),
        {"table": table, "name": name},
    ).scalar()
    return result is not None


def _rename(table: str, from_name: str, to_name: str) -> None:
    """
    Renames one check constraint, tolerating an already-renamed state.

    Raises when neither name is present — see the module docstring for why
    that case is fatal rather than a no-op.
    """
    if _constraint_exists(table, from_name):
        op.execute(
            f'ALTER TABLE {table} RENAME CONSTRAINT "{from_name}" TO "{to_name}"'
        )
        logger.info(
            "ARCH06_STEP1A | RENAMED | %s | %s -> %s", table, from_name, to_name
        )
        return

    if _constraint_exists(table, to_name):
        logger.info(
            "ARCH06_STEP1A | ALREADY_CORRECT | %s | %s", table, to_name
        )
        return

    raise RuntimeError(
        f"Neither '{from_name}' nor '{to_name}' exists on '{table}'. The check "
        "constraint has been dropped outside the migration chain, so this "
        "table is no longer enforcing an invariant the model declares. "
        "Investigate before re-running; do not skip this revision."
    )


def _assert_no_double_prefixes() -> None:
    """
    Postcondition for upgrade(): no `ck_%_ck_%` names remain schema-wide.

    Scoped to the whole schema rather than to the two tables above, so a third
    instance introduced by any other revision fails here rather than surviving
    to the next audit.
    """
    bind = op.get_bind()
    remaining = bind.execute(
        sa.text(
            r"""
            SELECT conrelid::regclass::text AS tbl, conname
              FROM pg_constraint
             WHERE contype = 'c'
               AND conname LIKE 'ck\_%\_ck\_%'
             ORDER BY 1, 2
            """
        )
    ).fetchall()

    if remaining:
        listed = ", ".join(f"{row.tbl}.{row.conname}" for row in remaining)
        raise RuntimeError(
            "Double-prefixed check constraints remain after the rename: "
            f"{listed}. A.1.6 must return zero rows before Step 1 can close."
        )

    logger.info("ARCH06_STEP1A | VERIFIED | ck_%%_ck_%% returns 0 rows")


def upgrade() -> None:
    for _table, _from, _to in _RENAMES:
        if len(_to) > _MAX_IDENTIFIER_LENGTH:
            raise RuntimeError(
                f"Target constraint name '{_to}' is {len(_to)} characters, "
                f"past PostgreSQL's {_MAX_IDENTIFIER_LENGTH}-character limit. "
                "PostgreSQL would truncate it silently and the model's "
                "rendered name would no longer match the database."
            )

    for _table, _from, _to in _RENAMES:
        _rename(_table, _from, _to)

    _assert_no_double_prefixes()


def downgrade() -> None:
    """
    Restores the malformed names.

    A genuine reversal, not a best effort: the constraint expressions were
    never touched, so renaming back returns the catalog to exactly the state
    revision 3cdea80e19f3 left it in. This exists so that a rollback of the
    ARCH-06 Step 1 deploy is a single `alembic downgrade -1` rather than a
    hand-written repair.
    """
    for table, malformed_name, correct_name in _RENAMES:
        _rename(table, correct_name, malformed_name)