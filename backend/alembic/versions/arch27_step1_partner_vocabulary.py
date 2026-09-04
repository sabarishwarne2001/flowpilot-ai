"""ARCH-27 Step 1 — audit vocabulary for partner tenancy and marketplace (EXPAND)

Revision ID: arch27_step1_partner_vocabulary
Revises: arch26_step2_warehouse_sync
Create Date: 2026-09-04

WHY THIS IS A SEPARATE MIGRATION FROM THE TABLES
================================================

`ALTER TYPE ... ADD VALUE` runs outside a transaction block, and PostgreSQL
refuses to let a newly added enum value be USED in the transaction that added
it. Folding the vocabulary into arch27_step2 works right up until the first
migration or backfill that writes an audit row in the same step, at which
point it fails with an error naming the enum rather than the cause.

ARCH-20, ARCH-22, ARCH-25 and ARCH-26 all split it this way. This follows that
precedent exactly.

THE ORGANIZATION ANCHOR (blocking finding B1)
=============================================

`audit_logs.organization_id` is NOT NULL with a foreign key to
`organizations`, and ARCH-07's row-level immutability trigger sits on the
table. A partner is a tier ABOVE organization, so the obvious reading of
"audit a partner event" is a row with no organization — which this schema
cannot represent, and which cannot be made representable without relaxing a
NOT NULL on a trigger-protected table that every isolation gate in the
codebase asserts against.

The resolution is structural rather than schema-relaxing: every partner
carries `partners.owner_organization_id`, the reseller's OWN tenant, and every
ARCH-27 audit row anchors to a real organization:

    PARTNER_CREATED     partners.owner_organization_id
    TENANT_ASSIGNED     the CLIENT organization being assigned or released —
                        its control changed, so its auditors are the readers
    REV_SHARE_SETTLED   partners.owner_organization_id
    MANIFEST_PUBLISHED  partners.owner_organization_id
    MANIFEST_INSTALLED  the INSTALLING organization

`details.partner_id` carries the partner on every one of them, so "everything
this partner did" is one indexed JSONB predicate away without a second audit
surface, a second immutability trigger, and a second retention policy to keep
in step with ARCH-20.

ACTIONS REUSED RATHER THAN ADDED
================================

`CREATED` / `UPDATED` / `DELETED` (marketplace item lifecycle), `ENABLED` /
`DISABLED` (an installation paused), `REVOKED` (a signing key withdrawn) and
`ROLE_CHANGED` (a partner member's role) all already exist and mean here
exactly what they mean everywhere else.

The five added below have no existing equivalent:

  PARTNER_CREATED     A reseller tier now exists above one or more tenants.
                      Distinct from CREATED because this is the row someone
                      reaches for when asking "when did a third party gain
                      standing over customer accounts?" — a question CREATED
                      cannot answer without filtering out every work item,
                      webhook and API key ever made.
  TENANT_ASSIGNED     An organization entered or left a book of business.
                      Emitted on BOTH directions, with `details.direction`
                      carrying which: a release is the more interesting event,
                      because a burst of assign/release pairs against varying
                      organizations is what book-scope probing looks like.
  REV_SHARE_SETTLED   A payout period was sealed and a digest written. This is
                      the money receipt, and it carries `details.digest` so
                      that "the statement the partner received is the
                      statement we computed" is answerable from the audit log
                      alone — the ARCH-15 invoice-sealing property, carried
                      forward.
  MANIFEST_PUBLISHED  A signed automation manifest entered the catalog. Held
                      separate from CREATED because the signature, not the
                      row, is the security event.
  MANIFEST_INSTALLED  A tenant admitted third-party workflow code into their
                      own automation engine. This is the highest-consequence
                      row in the phase and it must not share an action with
                      anything else.

RESOURCE TYPES
==============

Four, as scoped. `MARKETPLACE_ITEM` covers manifests, signatures and
installations rather than splitting into four types: unlike ARCH-26's
destination/schedule/run split — where run rows arrive several orders of
magnitude more often than credential rows and would drown them — a catalog
item, its manifests and its installs share a lifetime and a reader. The
`resource_id` on an install row is the item; `details.manifest_id` and
`details.installation_id` carry the finer grain.
"""

from __future__ import annotations

from alembic import op

revision = "arch27_step1_partner_vocabulary"
down_revision = "arch26_step2_warehouse_sync"
branch_labels = None
depends_on = None


#: Mirrored by `AuditResourceType` in app/models/audit_log.py.
#: verify_arch27.py G2 asserts the two agree; drift between them produces a
#: row PostgreSQL accepts and SQLAlchemy cannot load back.
NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "PARTNER",
    "PARTNER_AGREEMENT",
    "REV_SHARE_LEDGER",
    "MARKETPLACE_ITEM",
)

#: Mirrored by `AuditAction` in app/models/audit_log.py.
NEW_ACTIONS: tuple[str, ...] = (
    "PARTNER_CREATED",
    "TENANT_ASSIGNED",
    "REV_SHARE_SETTLED",
    "MANIFEST_PUBLISHED",
    "MANIFEST_INSTALLED",
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
    nothing emits them once arch27_step2 is reversed.
    """