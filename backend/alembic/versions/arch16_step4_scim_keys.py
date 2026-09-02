"""ARCH-16 Step 4 — scim_api_keys.

Revision ID: arch16_step4_scim_keys
Revises: arch16_step3_directory
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "arch16_step4_scim_keys"
down_revision = "arch16_step3_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("secret_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("previous_secret_hmac", sa.LargeBinary()),
        sa.Column("previous_secret_expires_at", sa.DateTime(timezone=True)),

        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("ARRAY['scim:users','scim:groups']::text[]")),

        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),

        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("previous_last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", postgresql.INET()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                ondelete="SET NULL"),

        sa.UniqueConstraint("key_prefix", name="uq_scim_key_prefix"),
        sa.CheckConstraint(
            "(previous_secret_hmac IS NULL) = (previous_secret_expires_at IS NULL)",
            name="ck_scim_key_overlap_pairs"),
        sa.CheckConstraint("array_length(scopes, 1) >= 1",
                           name="ck_scim_key_has_scope"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_reason IS NOT NULL",
                           name="ck_scim_key_revoked_has_reason"),
    )
    op.create_index("ix_scim_keys_live", "scim_api_keys",
                    ["organization_id"],
                    postgresql_where=sa.text("revoked_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_scim_keys_live", table_name="scim_api_keys")
    op.drop_table("scim_api_keys")
