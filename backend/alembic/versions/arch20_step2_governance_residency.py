"""ARCH-20 Step 2 — residency region, erasure tombstones, exports, retention (EXPAND)

Revision ID: arch20_step2_governance_residency
Revises: arch20_step1_audit_vocabulary
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch20_step2_governance_residency"
down_revision = "arch20_step1_audit_vocabulary"
branch_labels = None
depends_on = None

DATA_RESIDENCY_REGION_VALUES: tuple[str, ...] = ("US", "EU", "APAC", "GLOBAL")

COMPLIANCE_EXPORT_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "EXPIRED",
)

AUDIT_RETENTION_FLOOR_DAYS: int = 400
MINIMUM_RETENTION_DAYS: int = 30

_REGION_SQL = ", ".join(f"'{v}'" for v in DATA_RESIDENCY_REGION_VALUES)
_STATUS_SQL = ", ".join(f"'{v}'" for v in COMPLIANCE_EXPORT_STATUS_VALUES)


def upgrade() -> None:
    # 1. organizations.data_residency_region
    op.add_column(
        "organizations",
        sa.Column(
            "data_residency_region",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'GLOBAL'"),
            comment="Jurisdiction this tenant's object storage is pinned to.",
        ),
    )
    op.create_check_constraint(
        "ck_organizations_data_residency_region_vocabulary",
        "organizations",
        f"data_residency_region IN ({_REGION_SQL})",
    )
    op.create_index(
        "ix_organizations_data_residency_region",
        "organizations",
        ["data_residency_region"],
    )

    # 2. erased_subjects
    op.create_table(
        "erased_subjects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "subject_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="The anonymised users row.",
        ),
        sa.Column(
            "subject_email_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex of the lowercased address.",
        ),
        sa.Column("erasure_ticket", sa.String(length=120), nullable=False),
        sa.Column(
            "erased_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "erased_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Per-table destruction counts.",
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_erased_subjects_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name="fk_erased_subjects_subject_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["erased_by_user_id"],
            ["users.id"],
            name="fk_erased_subjects_erased_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(subject_email_hash) = 64",
            name="ck_erased_subjects_email_hash_is_sha256",
        ),
        sa.CheckConstraint(
            "length(erasure_ticket) > 0",
            name="ck_erased_subjects_ticket_not_blank",
        ),
    )
    op.create_index(
        "uq_erased_subjects_org_email_hash",
        "erased_subjects",
        ["organization_id", "subject_email_hash"],
        unique=True,
    )
    op.create_index(
        "ix_erased_subjects_organization_id_erased_at",
        "erased_subjects",
        ["organization_id", sa.text("erased_at DESC")],
    )

    # 3. compliance_exports
    op.create_table(
        "compliance_exports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "storage_key",
            sa.String(length=512),
            nullable=True,
            comment="Tenant key of the archive. NOT a download URL.",
        ),
        sa.Column(
            "residency_region",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'GLOBAL'"),
            comment="Region this archive was written to.",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_compliance_exports_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_compliance_exports_requested_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUS_SQL})",
            name="ck_compliance_exports_status_vocabulary",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_compliance_exports_size_non_negative",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR storage_key IS NOT NULL",
            name="ck_compliance_exports_complete_has_key",
        ),
        sa.CheckConstraint(
            f"residency_region IN ({_REGION_SQL})",
            name="ck_compliance_exports_region_vocabulary",
        ),
    )
    op.create_index(
        "ix_compliance_exports_organization_id_created_at",
        "compliance_exports",
        ["organization_id", sa.text("created_at DESC")],
    )

    # 4. retention_policies
    op.create_table(
        "retention_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "work_item_retention_days",
            sa.Integer(),
            nullable=True,
            comment="NULL means keep forever.",
        ),
        sa.Column(
            "audit_retention_days",
            sa.Integer(),
            nullable=True,
            comment="Floored at 400 by CHECK constraint.",
        ),
        sa.Column(
            "conversation_retention_days",
            sa.Integer(),
            nullable=True,
            comment="NULL means keep forever.",
        ),
        sa.Column(
            "auto_purge_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="FALSE by default. Opting into age-based purge is a decision.",
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_retention_policies_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            name="uq_retention_policies_organization_id",
        ),
        sa.CheckConstraint(
            f"audit_retention_days IS NULL "
            f"OR audit_retention_days >= {AUDIT_RETENTION_FLOOR_DAYS}",
            name="ck_retention_policies_audit_floor",
        ),
        sa.CheckConstraint(
            f"work_item_retention_days IS NULL "
            f"OR work_item_retention_days >= {MINIMUM_RETENTION_DAYS}",
            name="ck_retention_policies_work_item_minimum",
        ),
        sa.CheckConstraint(
            f"conversation_retention_days IS NULL "
            f"OR conversation_retention_days >= {MINIMUM_RETENTION_DAYS}",
            name="ck_retention_policies_conversation_minimum",
        ),
    )


def downgrade() -> None:
    op.drop_table("retention_policies")
    op.drop_index(
        "ix_compliance_exports_organization_id_created_at",
        table_name="compliance_exports",
    )
    op.drop_table("compliance_exports")
    op.drop_index(
        "ix_erased_subjects_organization_id_erased_at",
        table_name="erased_subjects",
    )
    op.drop_index("uq_erased_subjects_org_email_hash", table_name="erased_subjects")
    op.drop_table("erased_subjects")
    op.drop_index(
        "ix_organizations_data_residency_region", table_name="organizations"
    )
    op.drop_constraint(
        "ck_organizations_data_residency_region_vocabulary",
        "organizations",
        type_="check",
    )
    op.drop_column("organizations", "data_residency_region")
