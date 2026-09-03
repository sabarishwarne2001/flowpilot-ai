"""ARCH-25 Step 1 — audit vocabulary for custom domains and tenant branding (EXPAND)

Revision ID: arch25_step1_branding_vocabulary
Revises: arch24_step2_revenue_recognition
Create Date: 2026-09-03

WHY THIS IS A SEPARATE MIGRATION FROM THE TABLES
================================================

Invariant I5. `ALTER TYPE ... ADD VALUE` runs outside a transaction block, and
PostgreSQL refuses to let a newly added enum value be USED in the transaction
that added it. Folding the vocabulary into arch25_step2 works right up until
the first migration or data backfill that writes an audit row in the same
step, at which point it fails with an error naming the enum rather than the
cause.

ARCH-20 (arch20_step1_audit_vocabulary -> arch20_step2_governance_residency)
and ARCH-22 (arch22_step1_byok_vocabulary -> arch22_step2_byok_credentials)
both split it this way. This follows that precedent exactly.

ACTIONS REUSED RATHER THAN ADDED
================================

`CREATED` (a domain is claimed), `UPDATED` (branding tokens edited via a path
other than the branding endpoint), `DELETED` (a hostname is released), and
`DISABLED` (a sender domain lapses and the tenant degrades to the platform
sender) all already exist and already mean the right thing.

`DISABLED` for the lapsed sender is the one worth defending, because ARCH-25
invariant 5 requires the degradation to be VISIBLE. Visibility is delivered by
`tenant_branding.sender_domain_status = 'LAPSED'` — a status distinct from
'UNSET', so a lapsed domain can never be misread as one that was never
configured — plus the alert the service raises. The audit row is the record
that it happened, not the mechanism that surfaces it, and `SENDER_LAPSED`
alongside an existing `DISABLED` would give the audit reader two vocabularies
for one event. ARCH-22 made the same call and stated the same reason.

The four actions added here are the ones with no existing equivalent:

  DOMAIN_VERIFIED   A DNS TXT challenge succeeded. Distinct from UPDATED
                    because it is the single event that unlocks certificate
                    issuance, and a compliance reviewer asking "when did you
                    prove they owned this hostname?" needs one action to
                    filter on rather than an UPDATED with a details payload.
  DOMAIN_REVOKED    Host resolution for this hostname stops. Distinct from
                    DELETED: the row survives, so the hostname stays claimed
                    and cannot be picked up by another tenant.
  TLS_ISSUED        A certificate now exists for a customer-controlled
                    hostname. This is the highest-consequence event in the
                    phase and the one an incident review reaches for first.
  BRANDING_UPDATED  Tokens or assets changed. Distinct from UPDATED because
                    branding writes are admin-gated while domain writes are
                    owner-gated, and separating them lets the audit console
                    show the two role boundaries as two different streams.
"""

from __future__ import annotations

from alembic import op

revision = "arch25_step1_branding_vocabulary"
down_revision = "arch24_step2_revenue_recognition"
branch_labels = None
depends_on = None


#: Mirrored by `AuditResourceType` in app/models/audit_log.py.
#: verify_arch25.py G2 asserts the two agree; drift between them produces a
#: row PostgreSQL accepts and SQLAlchemy cannot load back.
NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "CUSTOM_DOMAIN",
    "TENANT_BRANDING",
)

#: Mirrored by `AuditAction` in app/models/audit_log.py.
NEW_ACTIONS: tuple[str, ...] = (
    "DOMAIN_VERIFIED",
    "DOMAIN_REVOKED",
    "TLS_ISSUED",
    "BRANDING_UPDATED",
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
    nothing emits them once arch25_step2 is reversed.
    """