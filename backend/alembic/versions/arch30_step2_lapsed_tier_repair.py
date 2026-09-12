"""ARCH-30 Tranche 3 Step 2 — release organizations pinned to a tier they stopped paying for.

Revision ID: arch30_step2_lapsed_tier_repair
Revises: arch30_step1_gateway_lifecycle_addons
Create Date: 2026-09-11

THE DEFECT
==========

`subscription_service.upsert_from_stripe` wrote `organizations.quota_tier_id`
from the subscription on every reconcile, whatever the status. A subscription
that ended left that pointer on the paid tier, and `quota_service.resolve_tier`
falls back to the pointer once no LIVE subscription exists. A cancelled
Business customer kept Business limits — and, since Tranche 2, any add-ons
Business bundles — for free, forever.

`subscription_service.apply_tier_for_status` stops new occurrences. This
migration repairs existing ones, with the same three conditions the service
uses, so it never touches a tier someone assigned deliberately:

    1. the organization's most recent subscription is canceled or
       incomplete_expired;
    2. it has no live subscription;
    3. its tier pointer still equals that subscription's tier — i.e. the
       pointer was written by propagation, not changed by an operator since.

The target is the newest published, active, in-force version of the lapsed
tier key (`free`, matching `BILLING_LAPSED_TIER_KEY`'s default). When no such
version exists the repair does nothing and says so, rather than inventing one.

Downgrade is a no-op. Re-pinning organizations to tiers they do not pay for
is the defect, not a state worth restoring.
"""

from __future__ import annotations

from alembic import op

revision = "arch30_step2_lapsed_tier_repair"
down_revision = "arch30_step1_gateway_lifecycle_addons"
branch_labels = None
depends_on = None

LAPSED_TIER_KEY = "free"

REPAIR_SQL = f"""
DO $$
DECLARE
    target uuid;
    repaired integer;
BEGIN
    SELECT id INTO target
      FROM quota_tiers
     WHERE key = '{LAPSED_TIER_KEY}'
       AND is_active
       AND published_at IS NOT NULL
       AND effective_from <= now()
     ORDER BY version DESC
     LIMIT 1;

    IF target IS NULL THEN
        RAISE NOTICE 'arch30_step2: no published in-force ''{LAPSED_TIER_KEY}'' tier; nothing repaired';
        RETURN;
    END IF;

    WITH latest AS (
        SELECT DISTINCT ON (ba.organization_id)
               ba.organization_id, s.status, s.quota_tier_id
          FROM subscriptions s
          JOIN billing_accounts ba ON ba.id = s.billing_account_id
         ORDER BY ba.organization_id, s.created_at DESC
    ),
    live AS (
        SELECT DISTINCT ba.organization_id
          FROM subscriptions s
          JOIN billing_accounts ba ON ba.id = s.billing_account_id
         WHERE s.status IN ('trialing', 'active', 'past_due', 'unpaid')
    )
    UPDATE organizations o
       SET quota_tier_id = target
      FROM latest l
     WHERE o.id = l.organization_id
       AND l.status IN ('canceled', 'incomplete_expired')
       AND o.quota_tier_id = l.quota_tier_id
       AND o.quota_tier_id IS DISTINCT FROM target
       AND NOT EXISTS (SELECT 1 FROM live WHERE live.organization_id = o.id);

    GET DIAGNOSTICS repaired = ROW_COUNT;
    RAISE NOTICE 'arch30_step2: % organization(s) moved to the lapsed tier', repaired;
END $$;
"""


def upgrade() -> None:
    op.execute(REPAIR_SQL)


def downgrade() -> None:
    pass
