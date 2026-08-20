"""ARCH-14 Step 4 — quota_tiers, quota_tier_entries, organizations.quota_tier_id (EXPAND)

Revision ID: arch14_step4_quota_tiers
Revises: arch14_step2_rollups
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch14_step4_quota_tiers"
down_revision = "arch14_step2_rollups"
branch_labels = None
depends_on = None


TIER_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION quota_tiers_publish_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% is published and cannot be deleted; '
                'publish a superseding version instead',
                OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    IF (NEW.effective_to IS DISTINCT FROM OLD.effective_to) THEN
        IF OLD.effective_to IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% already closed at %; a closed window is '
                'immutable',
                OLD.key, OLD.version, OLD.effective_to
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to IS NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% cannot be re-opened', OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to <= OLD.effective_from THEN
            RAISE EXCEPTION
                'quota_tiers %/v% effective_to % precedes effective_from %',
                OLD.key, OLD.version, NEW.effective_to, OLD.effective_from
                USING ERRCODE = '22007';
        END IF;
    END IF;

    IF (NEW.is_active IS DISTINCT FROM OLD.is_active) AND NEW.is_active THEN
        RAISE EXCEPTION
            'quota_tiers %/v% cannot be reactivated once deactivated',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    IF (
        NEW.id                      IS DISTINCT FROM OLD.id
        OR NEW.key                  IS DISTINCT FROM OLD.key
        OR NEW.display_name         IS DISTINCT FROM OLD.display_name
        OR NEW.version              IS DISTINCT FROM OLD.version
        OR NEW.effective_from       IS DISTINCT FROM OLD.effective_from
        OR NEW.published_at         IS DISTINCT FROM OLD.published_at
        OR NEW.published_by_user_id IS DISTINCT FROM OLD.published_by_user_id
        OR NEW.notes                IS DISTINCT FROM OLD.notes
        OR NEW.details              IS DISTINCT FROM OLD.details
        OR NEW.created_at           IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'quota_tiers %/v% is published and immutable; only effective_to '
            '(once, forward) and is_active (once, to false) may change',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


TIER_ENTRY_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION quota_tier_entries_publish_immutable()
RETURNS TRIGGER AS $$
DECLARE
    parent_published TIMESTAMPTZ;
    parent_key       TEXT;
    parent_version   INTEGER;
    target_tier      UUID;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        target_tier := OLD.quota_tier_id;
    ELSE
        target_tier := NEW.quota_tier_id;
    END IF;

    SELECT qt.published_at, qt.key, qt.version
      INTO parent_published, parent_key, parent_version
      FROM quota_tiers qt
     WHERE qt.id = target_tier;

    IF NOT FOUND THEN
        RETURN COALESCE(OLD, NEW);
    END IF;

    IF parent_published IS NOT NULL THEN
        RAISE EXCEPTION
            'quota_tier_entries for published tier %/v% are immutable '
            '(attempted %); publish a superseding version instead',
            parent_key, parent_version, TG_OP
            USING ERRCODE = '42501';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "quota_tiers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["users.id"],
            name="fk_quota_tiers_published_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("version > 0", name="ck_quota_tiers_version_positive"),
        sa.CheckConstraint("length(key) > 0", name="ck_quota_tiers_key_not_blank"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_quota_tiers_effective_window_ordered",
        ),
        sa.CheckConstraint(
            "NOT is_active OR published_at IS NOT NULL",
            name="ck_quota_tiers_active_implies_published",
        ),
    )

    op.create_index(
        "uq_quota_tiers_key_version", "quota_tiers", ["key", "version"], unique=True
    )
    op.execute(
        "CREATE INDEX ix_quota_tiers_key_effective "
        "ON quota_tiers (key, effective_from, effective_to) "
        "WHERE is_active AND published_at IS NOT NULL"
    )

    op.create_table(
        "quota_tier_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("quota_tier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("limit_key", sa.String(length=64), nullable=False),
        sa.Column(
            "period",
            postgresql.ENUM(
                "DAY", "MONTH", name="spend_limit_period", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'MONTH'::spend_limit_period"),
        ),
        sa.Column("max_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("max_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column(
            "overage_policy",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'REFUSE'"),
        ),
        sa.Column(
            "overage_price_tier_key", sa.String(length=64), nullable=True
        ),
        sa.Column("grace_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["quota_tier_id"],
            ["quota_tiers.id"],
            name="fk_quota_tier_entries_quota_tier_id_quota_tiers",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "max_quantity IS NOT NULL OR max_cost_micros IS NOT NULL",
            name="ck_quota_tier_entries_at_least_one_ceiling",
        ),
        sa.CheckConstraint(
            "max_quantity IS NULL OR max_quantity >= 0",
            name="ck_quota_tier_entries_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "max_cost_micros IS NULL OR max_cost_micros >= 0",
            name="ck_quota_tier_entries_cost_non_negative",
        ),
        sa.CheckConstraint(
            "grace_quantity IS NULL OR grace_quantity >= 0",
            name="ck_quota_tier_entries_grace_non_negative",
        ),
        sa.CheckConstraint(
            "length(limit_key) > 0",
            name="ck_quota_tier_entries_limit_key_not_blank",
        ),
        sa.CheckConstraint(
            "overage_policy IN ('REFUSE', 'ALLOW_AND_BILL', 'ALLOW_AND_WARN')",
            name="ck_quota_tier_entries_overage_policy_known",
        ),
        sa.CheckConstraint(
            "overage_policy <> 'ALLOW_AND_BILL' "
            "OR overage_price_tier_key IS NOT NULL",
            name="ck_quota_tier_entries_allow_and_bill_requires_price",
        ),
    )

    op.create_index(
        "uq_quota_tier_entries_scope",
        "quota_tier_entries",
        ["quota_tier_id", "limit_key", "period"],
        unique=True,
    )

    op.add_column(
        "organizations",
        sa.Column("quota_tier_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_organizations_quota_tier_id_quota_tiers",
        "organizations",
        "quota_tiers",
        ["quota_tier_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "CREATE INDEX ix_organizations_quota_tier_id "
        "ON organizations (quota_tier_id) WHERE quota_tier_id IS NOT NULL"
    )

    op.execute(TIER_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_quota_tiers_publish_immutable
        BEFORE UPDATE OR DELETE ON quota_tiers
        FOR EACH ROW EXECUTE FUNCTION quota_tiers_publish_immutable();
        """
    )

    op.execute(TIER_ENTRY_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_quota_tier_entries_publish_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON quota_tier_entries
        FOR EACH ROW EXECUTE FUNCTION quota_tier_entries_publish_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quota_tier_entries_publish_immutable "
        "ON quota_tier_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS quota_tier_entries_publish_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quota_tiers_publish_immutable ON quota_tiers"
    )
    op.execute("DROP FUNCTION IF EXISTS quota_tiers_publish_immutable()")

    op.execute("DROP INDEX IF EXISTS ix_organizations_quota_tier_id")
    op.drop_constraint(
        "fk_organizations_quota_tier_id_quota_tiers",
        "organizations",
        type_="foreignkey",
    )
    op.drop_column("organizations", "quota_tier_id")

    op.drop_table("quota_tier_entries")
    op.drop_table("quota_tiers")