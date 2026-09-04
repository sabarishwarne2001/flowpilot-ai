"""ARCH-26 Step 1 — audit vocabulary for warehouse sync and BI egress (EXPAND)

Revision ID: arch26_step1_export_vocabulary
Revises: arch25_step2_custom_domains
Create Date: 2026-09-03

WHY THIS IS A SEPARATE MIGRATION FROM THE TABLES
================================================

Invariant I5. `ALTER TYPE ... ADD VALUE` runs outside a transaction block, and
PostgreSQL refuses to let a newly added enum value be USED in the transaction
that added it. Folding the vocabulary into arch26_step2 works right up until
the first migration or backfill that writes an audit row in the same step, at
which point it fails with an error naming the enum rather than the cause.

ARCH-20 (arch20_step1_audit_vocabulary -> arch20_step2_governance_residency),
ARCH-22 (arch22_step1_byok_vocabulary -> arch22_step2_byok_credentials) and
ARCH-25 (arch25_step1_branding_vocabulary -> arch25_step2_custom_domains) all
split it this way. This follows that precedent exactly.

ACTIONS REUSED RATHER THAN ADDED
================================

`UPDATED` (a destination's label or dataset selection is edited), `DELETED` (a
destination is removed), `ENABLED` / `DISABLED` (a schedule is paused or
resumed) and `EXPORTED` (ARCH-20's compliance export) all already exist.

`EXPORTED` is the one worth defending as NOT reused for a warehouse push.
ARCH-20 emits it when an operator downloads a compliance bundle: a human
pulling data out of the platform under a legal obligation. A warehouse sync is
a scheduled machine push into infrastructure the tenant controls, and a
compliance reviewer filtering `EXPORTED` to answer "what left the platform by
human hand?" must not have to subtract several thousand cron-driven rows to
get the answer. Two different questions, two different actions.

The five actions added here have no existing equivalent:

  DESTINATION_CREATED  A tenant registered a warehouse and handed us a
                       credential for it. Distinct from CREATED because this
                       is the row a security reviewer reaches for when asking
                       "when did we start holding a key to their Snowflake?"
  DESTINATION_TESTED   A connection probe ran against a tenant-supplied
                       hostname. Recorded on both outcomes — a probe that
                       fails is the more interesting one, because a burst of
                       them against varying hostnames is what credential
                       spraying through our egress looks like.
  SYNC_TRIGGERED       A run started, whether by schedule or by the console's
                       "Sync now" button. `details.trigger` carries which.
  SYNC_COMPLETED       Rows left the platform. This is the delivery receipt,
                       and it carries the bundle digest so that "the bundle a
                       tenant downloaded is the bundle we digested" is
                       answerable from the audit log alone.
  SYNC_FAILED          A run terminated without delivering. Separate from
                       SYNC_COMPLETED with an outcome of DENIED because
                       hardening invariant 5 requires a failed sync to be
                       findable, and `outcome` is already carrying the
                       permission dimension for every other row in the table.

RESOURCE TYPES
==============

Three, not one. A destination holds a credential, a schedule holds a cadence,
and a run holds an outcome; they have different lifetimes and different
readers. Collapsing them into a single `WAREHOUSE_SYNC` resource type would
mean every audit query against a destination's credential history also has to
filter out run rows, which arrive several orders of magnitude more often.
"""

from __future__ import annotations

from alembic import op

revision = "arch26_step1_export_vocabulary"
down_revision = "arch25_step2_custom_domains"
branch_labels = None
depends_on = None


#: Mirrored by `AuditResourceType` in app/models/audit_log.py.
#: verify_arch26.py G2 asserts the two agree; drift between them produces a
#: row PostgreSQL accepts and SQLAlchemy cannot load back.
NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "WAREHOUSE_DESTINATION",
    "EXPORT_SCHEDULE",
    "EXPORT_SYNC_RUN",
)

#: Mirrored by `AuditAction` in app/models/audit_log.py.
NEW_ACTIONS: tuple[str, ...] = (
    "DESTINATION_CREATED",
    "DESTINATION_TESTED",
    "SYNC_TRIGGERED",
    "SYNC_COMPLETED",
    "SYNC_FAILED",
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
    nothing emits them once arch26_step2 is reversed.
    """