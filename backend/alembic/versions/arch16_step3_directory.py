"""ARCH-16 Step 3 — directory_identities, scim_groups, scim_group_members.

Revision ID: arch16_step3_directory
Revises: arch16_step2_idp_configs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "arch16_step3_directory"
down_revision = "arch16_step2_idp_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    workspace_role = postgresql.ENUM(name="workspace_role", create_type=False)

    op.create_table(
        "directory_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name_id_format", sa.Text()),
        sa.Column("user_name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),

        sa.Column("attributes", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),

        sa.Column("provisioned_via", sa.Text(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("deprovisioned_at", sa.DateTime(timezone=True)),
        sa.Column("deprovision_reason", sa.Text()),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),

        sa.UniqueConstraint("idp_config_id", "external_id",
                            name="uq_directory_external"),
        sa.UniqueConstraint("idp_config_id", "user_id", name="uq_directory_user"),
        sa.CheckConstraint("active = true OR deprovisioned_at IS NOT NULL",
                           name="ck_directory_inactive_has_timestamp"),
        sa.CheckConstraint("provisioned_via IN ('JIT','SCIM','INVITATION')",
                           name="ck_directory_provisioned_via"),
        sa.CheckConstraint("user_name = lower(user_name)",
                           name="ck_directory_username_lower"),
    )
    op.create_index("ix_directory_org_active", "directory_identities",
                    ["organization_id", "active"])
    op.create_index("ix_directory_username", "directory_identities",
                    ["idp_config_id", "user_name"])
    op.create_index("ix_directory_deprovisioned", "directory_identities",
                    ["deprovisioned_at"],
                    postgresql_where=sa.text("deprovisioned_at IS NOT NULL"))

    op.create_table(
        "scim_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.Column("workspace_role", workspace_role),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("idp_config_id", "external_id",
                            name="uq_scim_group_external"),
        sa.CheckConstraint("(workspace_id IS NULL) = (workspace_role IS NULL)",
                           name="ck_scim_group_binding_pairs"),
    )
    op.create_index("ix_scim_groups_org", "scim_groups", ["organization_id"])

    op.create_table(
        "scim_group_members",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["group_id"], ["scim_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_id"], ["directory_identities.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "identity_id"),
    )
    op.create_index("ix_scim_group_members_identity", "scim_group_members",
                    ["identity_id"])


def downgrade() -> None:
    op.drop_index("ix_scim_group_members_identity", table_name="scim_group_members")
    op.drop_table("scim_group_members")
    op.drop_index("ix_scim_groups_org", table_name="scim_groups")
    op.drop_table("scim_groups")
    op.drop_index("ix_directory_deprovisioned", table_name="directory_identities")
    op.drop_index("ix_directory_username", table_name="directory_identities")
    op.drop_index("ix_directory_org_active", table_name="directory_identities")
    op.drop_table("directory_identities")
