"""Campaign session 4 — drop the workspace `email_settings` table, read by nothing since ARCH-40.

Revision ID: s4b1_drop_email_settings
Revises: s4a3_review_view_drops_deleted_checks
Create Date: 2026-10-14

ARCH-40 moved workspace mail to `workspace_email_overrides` (`arch40_step2_settings_backfill`
copied every row across) and the old table kept its rows with no reader and no writer. Its model,
CRUD module and schemas are removed with this revision.

Upgrade archives every remaining row, whole, into `settings_migration_archive` (settings_kind
EMAIL, this revision), the pattern ARCH-40 used, so nothing is lost; then drops the table. The
`email_encryption` enum stays: `organization_email_settings.encryption` uses it.

Downgrade recreates the table exactly as it was (columns, defaults, primary key, both indexes,
both foreign keys, under their names) and puts back the rows this revision archived (a row whose
workspace has since been deleted stays archived, as the cascade would have removed it; a person
deleted since is cleared from `updated_by_user_id`, as SET NULL would have done).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s4b1_drop_email_settings"
down_revision = "s4a3_review_view_drops_deleted_checks"
branch_labels = None
depends_on = None

_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO settings_migration_archive (
            id, settings_kind, source_row_id, source_user_id, source_user_email,
            workspace_id, winning_row_id, payload, migration_revision, archived_at
        )
        SELECT
            gen_random_uuid(),
            'EMAIL',
            es.id,
            coalesce(es.updated_by_user_id, '{_NIL_UUID}'::uuid),
            coalesce(u.email, 'unknown@migration.invalid'),
            es.workspace_id,
            NULL,
            (row_to_json(es)::jsonb || '{{"source_table": "email_settings"}}'::jsonb)::json,
            '{revision}',
            now()
        FROM email_settings es
        LEFT JOIN users u ON u.id = es.updated_by_user_id
        """
    )
    op.drop_table("email_settings")


def downgrade() -> None:
    op.create_table(
        "email_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_username", sa.String(length=255), nullable=False),
        sa.Column("encrypted_password", sa.String(length=512), nullable=False),
        sa.Column("sender_name", sa.String(length=100), nullable=False),
        sa.Column(
            "encryption",
            postgresql.ENUM("NONE", "TLS", "SSL", name="email_encryption", create_type=False),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_email_settings_updated_by_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_email_settings_workspace_id_workspaces", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_settings"),
    )
    op.create_index("ix_email_settings_is_enabled", "email_settings", ["is_enabled"], unique=False)
    op.create_index("ix_email_settings_workspace_id", "email_settings", ["workspace_id"], unique=True)

    op.execute(
        f"""
        INSERT INTO email_settings (
            id, smtp_host, smtp_port, smtp_username, encrypted_password, sender_name,
            encryption, is_enabled, created_at, updated_at, workspace_id, updated_by_user_id
        )
        SELECT
            r.id, r.smtp_host, r.smtp_port, r.smtp_username, r.encrypted_password, r.sender_name,
            r.encryption, r.is_enabled, r.created_at, r.updated_at, r.workspace_id,
            CASE WHEN EXISTS (SELECT 1 FROM users u WHERE u.id = r.updated_by_user_id)
                 THEN r.updated_by_user_id END
        FROM settings_migration_archive a
        CROSS JOIN LATERAL json_populate_record(NULL::email_settings, a.payload) r
        WHERE a.migration_revision = '{revision}'
          AND a.settings_kind = 'EMAIL'
          AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = r.workspace_id)
        """
    )
    op.execute(
        f"""
        DELETE FROM settings_migration_archive a
         WHERE a.migration_revision = '{revision}'
           AND a.settings_kind = 'EMAIL'
           AND EXISTS (SELECT 1 FROM email_settings es WHERE es.id = a.source_row_id)
        """
    )
