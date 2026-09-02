"""ARCH-22 Step 1 — audit vocabulary for BYOK credentials and model routing (EXPAND)

Revision ID: arch22_step1_byok_vocabulary
Revises: arch21_step1_public_api_tiers
Create Date: 2026-09-01

WHY THIS IS A SEPARATE MIGRATION FROM THE TABLES
================================================

`ALTER TYPE ... ADD VALUE` runs outside a transaction block, and PostgreSQL
refuses to let a newly added enum value be USED in the transaction that added
it. Folding the vocabulary into arch22_step2 would work right up until the
first migration that inserts an audit row in the same step, at which point it
fails with an error that names the enum rather than the cause.

ARCH-20 split this the same way (arch20_step1_audit_vocabulary ->
arch20_step2_governance_residency). This follows that precedent exactly.

Actions reused rather than added: ROTATED (credential rotation), CREATED,
UPDATED and DELETED (routing rules), ENABLED and DISABLED (activation). Adding
CREDENTIAL_CREATED alongside an existing CREATED would give the audit reader
two vocabularies for one event.
"""

from __future__ import annotations

from alembic import op

revision = "arch22_step1_byok_vocabulary"
down_revision = "arch21_step1_public_api_tiers"
branch_labels = None
depends_on = None


NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "PROVIDER_CREDENTIAL",
    "MODEL_ROUTE",
)

NEW_ACTIONS: tuple[str, ...] = (
    # A live round trip against the provider succeeded or failed. Distinct
    # from UPDATED: validation changes no tenant-visible configuration, but it
    # is exactly the event a compliance reviewer looks for when asking "when
    # did you last prove this key still works?"
    "CREDENTIAL_VALIDATED",
    # The tenant changed whether a failed BYOK call may silently reach the
    # platform's own provider account. This is the single most consequential
    # toggle in the phase and deserves its own action rather than an UPDATED.
    "FALLBACK_POLICY_CHANGED",
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
    nothing emits them once arch22_step2 is reversed.
    """
