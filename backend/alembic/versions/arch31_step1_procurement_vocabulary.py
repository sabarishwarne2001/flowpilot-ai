"""ARCH-31 Step 1 — audit vocabulary for procurement matching (EXPAND)

Revision ID: arch31_step1_procurement_vocabulary
Revises: arch31_step0_document_roles
Create Date: 2026-09-14

WHY THIS IS A SEPARATE MIGRATION FROM THE TABLES
================================================

`ALTER TYPE ... ADD VALUE` runs outside a transaction block, and PostgreSQL
refuses to let a newly added enum value be USED in the transaction that added
it. Folding the vocabulary into arch31_step1_procurement_matching would work
right up until the first migration or backfill that writes an audit row in the
same step, at which point it fails with an error naming the enum rather than
the cause.

ARCH-20, ARCH-22 and ARCH-26 all split this the same way. This follows that
precedent exactly.

WHICH ACTIONS ARE NEW AND WHICH ARE REUSED
==========================================

Reused, deliberately: CREATED and UPDATED for tolerance policy drafts. A draft
is an ordinary mutable row and reads correctly under the generic actions.

New, because each answers a question the generic actions cannot:

  MATCH_APPROVED      A human accepted a case that the engine scored. This is
                      the row somebody reaches for when asking "who authorised
                      payment against this invoice?", and ACCEPTED cannot
                      answer it without filtering out every invitation ever
                      accepted.

  MATCH_DISPUTED      The mirror. Kept distinct from DECLINED for the same
                      reason.

  MATCH_RESCORED      A case was re-scored and the previous case superseded.
                      Distinct from UPDATED because the old case was not
                      edited — it was retired and replaced, and a reviewer
                      looking at a SUPERSEDED row needs to find the event that
                      retired it.

  TOLERANCE_PUBLISHED A draft policy became immutable and started governing
                      every subsequent score. The single highest-consequence
                      action in the phase: it changes what counts as a
                      variance for every future case, and the database
                      forbids editing it afterwards.

MATCH_SCORED is deliberately NOT here. Scoring is machine work that happens on
every eligible document set, and an audit row per score would bury the four
actions above under volume. The score is recorded on the case row itself
(`input_digest`, `policy_version`, `updated_at`) and emitted as an ARCH-09
outbox event; neither of those is an access-control record.
"""

from __future__ import annotations

from alembic import op

revision = "arch31_step1_procurement_vocabulary"
down_revision = "arch31_step0_document_roles"
branch_labels = None
depends_on = None


NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "PROCUREMENT_CASE",
    "PROCUREMENT_TOLERANCE_POLICY",
)

NEW_ACTIONS: tuple[str, ...] = (
    "MATCH_APPROVED",
    "MATCH_DISPUTED",
    "MATCH_RESCORED",
    "TOLERANCE_PUBLISHED",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )
        for value in NEW_ACTIONS:
            op.execute(
                f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    """No-op, and deliberately so.

    PostgreSQL cannot remove a value from an enum type. Dropping and
    recreating `audit_action` would require rewriting every audit_logs row,
    and audit_logs carries a row-level immutability trigger (ARCH-07 Step 4)
    that would reject the rewrite. Leaving the values in place is harmless:
    nothing emits them once arch31_step1_procurement_matching is reversed.
    """