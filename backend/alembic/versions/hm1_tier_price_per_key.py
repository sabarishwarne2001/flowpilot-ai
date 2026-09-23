"""HARDENING-MASTER HM-S1 — a gateway price identifies a plan, not a version.

Revision ID: hm1_tier_price_per_key
Revises: arch40_step2a_review_view_paths

WHY
===

ARCH-29 made `quota_tiers.gateway_price_id` globally unique, so that two
PLANS could never share one gateway price (finding F-2: every tier charged at
one global price). The index also stopped two VERSIONS of the same plan from
sharing a price, and a published version is immutable, so adding a feature to
Developer at an unchanged $49 required a new product at the gateway, and moving
existing subscribers onto the new version would then have pointed their seat
syncs at a product their gateway subscription is not on.

This replaces the unique index with the rule F-2 actually needed: one gateway
price maps to one plan KEY. A trigger enforces it (a unique index cannot say
"unique per key"), under a transaction-scoped advisory lock so two concurrent
publishes cannot both pass the check.

CHAIN POSITION
==============

Inserted between arch40_step2a_review_view_paths (the ARCH-40 release head)
and arch40_step3_contract_ai_settings (the flag-gated contract step), whose
down_revision now names this revision. `run_hardening_master.ps1` upgrades to
this revision explicitly, so the lossy contract step still never runs unasked.

DOWNGRADE
=========

Restores the global unique index. It fails if two versions of one plan share
a price by then, which is the correct refusal: the data no longer fits it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "hm1_tier_price_per_key"
down_revision = "arch40_step2a_review_view_paths"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION quota_tiers_price_id_single_key() RETURNS trigger AS $$
BEGIN
    IF NEW.gateway_price_id IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('quota_tiers.gateway_price_id'));
    IF EXISTS (
        SELECT 1 FROM quota_tiers
        WHERE gateway_price_id = NEW.gateway_price_id
          AND key <> NEW.key
          AND id <> NEW.id
    ) THEN
        RAISE EXCEPTION 'gateway price % is already bound to another plan; one price identifies one plan', NEW.gateway_price_id
            USING ERRCODE = '23505';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.drop_index("uq_quota_tiers_gateway_price_id", table_name="quota_tiers")
    op.create_index(
        "ix_quota_tiers_gateway_price_id",
        "quota_tiers",
        ["gateway_price_id"],
        unique=False,
        postgresql_where=sa.text("gateway_price_id IS NOT NULL"),
    )
    op.execute(FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_quota_tiers_price_id_single_key "
        "BEFORE INSERT OR UPDATE OF gateway_price_id, key ON quota_tiers "
        "FOR EACH ROW EXECUTE FUNCTION quota_tiers_price_id_single_key()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_quota_tiers_price_id_single_key ON quota_tiers")
    op.execute("DROP FUNCTION IF EXISTS quota_tiers_price_id_single_key()")
    op.drop_index("ix_quota_tiers_gateway_price_id", table_name="quota_tiers")
    op.create_index(
        "uq_quota_tiers_gateway_price_id",
        "quota_tiers",
        ["gateway_price_id"],
        unique=True,
        postgresql_where=sa.text("gateway_price_id IS NOT NULL"),
    )
