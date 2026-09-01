"""ARCH-21 Step 1 — public API rate tiers, gateway scopes, daily usage rollup (EXPAND)

Revision ID: arch21_step1_public_api_tiers
Revises: arch20_step2_governance_residency
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch21_step1_public_api_tiers"
down_revision = "arch20_step2_governance_residency"
branch_labels = None
depends_on = None

API_RATE_TIER_VALUES: tuple[str, ...] = ("FREE", "BUILDER", "PRO", "ENTERPRISE")
SCOPE_CONSTRAINT_NAME = "ck_api_keys_scopes_allowed"

SCOPES_12: tuple[str, ...] = (
    "organizations:read",
    "workspaces:read",
    "workspaces:write",
    "members:read",
    "work_items:read",
    "work_items:write",
    "audit_logs:read",
    "files:read",
    "files:write",
    "webhooks:read",
    "webhooks:write",
    "webhooks:admin",
)

SCOPES_17: tuple[str, ...] = SCOPES_12 + (
    "billing:read",
    "public_documents:read",
    "public_query:write",
    "public_workflows:read",
    "public_workflows:write",
)

LATENCY_BOUNDS_MS: tuple[float, ...] = (
    10.0, 25.0, 50.0, 80.0, 100.0, 200.0, 300.0, 500.0,
    800.0, 1200.0, 2000.0, 3000.0, 5000.0, 8000.0, 15000.0, 30000.0,
)

_TIER_SQL = ", ".join(f"'{v}'" for v in API_RATE_TIER_VALUES)
_EMPTY_BUCKETS = "[" + ", ".join("0" for _ in LATENCY_BOUNDS_MS) + "]"


def _array_sql(scopes: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{s}'" for s in scopes) + "]::text[]"


def upgrade() -> None:
    # 1. api_keys — tier attributes
    op.add_column(
        "api_keys",
        sa.Column(
            "tier_key",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'FREE'"),
            comment="ARCH-21 operational throughput tier.",
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "rate_limit_per_minute",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("60"),
            comment="Denormalised rate limit per minute.",
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "monthly_request_quota",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("10000"),
            comment="Calendar-month ceiling on billable gateway requests.",
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "is_public_api_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Explicit opt-in for public API gateway usage.",
        ),
    )

    op.create_check_constraint(
        "ck_api_keys_tier_key_vocabulary",
        "api_keys",
        f"tier_key IN ({_TIER_SQL})",
    )
    op.create_check_constraint(
        "ck_api_keys_rate_limit_positive",
        "api_keys",
        "rate_limit_per_minute > 0",
    )
    op.create_check_constraint(
        "ck_api_keys_monthly_quota_positive",
        "api_keys",
        "monthly_request_quota > 0",
    )
    op.create_check_constraint(
        "ck_api_keys_public_enabled_requires_scope",
        "api_keys",
        "NOT is_public_api_enabled OR scopes && ARRAY["
        "'public_documents:read','public_query:write',"
        "'public_workflows:read','public_workflows:write']::text[]",
    )

    op.create_index(
        "ix_api_keys_public_api_enabled",
        "api_keys",
        ["organization_id", "tier_key"],
        postgresql_where=sa.text("is_public_api_enabled"),
    )

    # 2. Scope vocabulary repair (D1)
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_ck_api_keys_scopes_allowed")
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_within_vocabulary")
    op.execute(f"ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS {SCOPE_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE api_keys ADD CONSTRAINT {SCOPE_CONSTRAINT_NAME} "
        f"CHECK (scopes <@ {_array_sql(SCOPES_17)})"
    )

    # 3. api_key_usage_daily
    op.create_table(
        "api_key_usage_daily",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "request_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "error_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "throttled_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_latency_ms",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "latency_bucket_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(f"'{_EMPTY_BUCKETS}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("request_count >= 0", name="ck_api_key_usage_daily_request_count_non_negative"),
        sa.CheckConstraint("error_count >= 0", name="ck_api_key_usage_daily_error_count_non_negative"),
        sa.CheckConstraint("throttled_count >= 0", name="ck_api_key_usage_daily_throttled_non_negative"),
        sa.CheckConstraint("total_latency_ms >= 0", name="ck_api_key_usage_daily_latency_non_negative"),
        sa.CheckConstraint(
            "throttled_count <= error_count",
            name="ck_api_key_usage_daily_throttles_within_errors",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(latency_bucket_counts) = 'array'",
            name="ck_api_key_usage_daily_buckets_are_array",
        ),
        sa.CheckConstraint(
            f"jsonb_array_length(latency_bucket_counts) = {len(LATENCY_BOUNDS_MS)}",
            name="ck_api_key_usage_daily_buckets_arity",
        ),
    )

    op.create_index(
        "uq_api_key_usage_daily_key_date",
        "api_key_usage_daily",
        ["api_key_id", "usage_date"],
        unique=True,
    )
    op.create_index(
        "ix_api_key_usage_daily_org_date",
        "api_key_usage_daily",
        ["organization_id", "usage_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_key_usage_daily_org_date", table_name="api_key_usage_daily")
    op.drop_index("uq_api_key_usage_daily_key_date", table_name="api_key_usage_daily")
    op.drop_table("api_key_usage_daily")

    op.execute(
        "UPDATE api_keys SET scopes = ARRAY(SELECT unnest(scopes) "
        f"INTERSECT SELECT unnest({_array_sql(SCOPES_12)})) "
        f"WHERE NOT (scopes <@ {_array_sql(SCOPES_12)})"
    )
    op.execute(
        "UPDATE api_keys SET deactivated_at = now(), "
        "deactivated_reason = 'SCOPE_DOWNGRADE', "
        "scopes = ARRAY['organizations:read']::text[] "
        "WHERE array_length(scopes, 1) IS NULL OR array_length(scopes, 1) < 1"
    )
    op.execute(f"ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS {SCOPE_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE api_keys ADD CONSTRAINT {SCOPE_CONSTRAINT_NAME} "
        f"CHECK (scopes <@ {_array_sql(SCOPES_12)})"
    )

    op.drop_index("ix_api_keys_public_api_enabled", table_name="api_keys")
    op.drop_constraint("ck_api_keys_public_enabled_requires_scope", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_monthly_quota_positive", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_rate_limit_positive", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_tier_key_vocabulary", "api_keys", type_="check")
    op.drop_column("api_keys", "is_public_api_enabled")
    op.drop_column("api_keys", "monthly_request_quota")
    op.drop_column("api_keys", "rate_limit_per_minute")
    op.drop_column("api_keys", "tier_key")