"""arch08_step8_api_keys_expand

ARCH-08 Step 8 — EXPAND. Creates api_keys table, adds audit_logs.api_key_id FK,
and non-blocking CHECK constraint ck_audit_logs_actor_xor_api_key.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "arch08_step8_api_keys_expand"
down_revision: Union[str, None] = "arch08_step5_outcome_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create api_keys table
    op.create_table(
        "api_keys",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("scopes", sa.dialects.postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_reason", sa.String(length=40), nullable=True),
        sa.Column("previous_secret_hash", sa.String(length=64), nullable=True, unique=True),
        sa.Column("previous_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "scopes <@ ARRAY["
            "'organizations:read','workspaces:read','workspaces:write',"
            "'members:read','work_items:read','work_items:write',"
            "'audit_logs:read','files:read','files:write']::text[]",
            name="ck_api_keys_scopes_allowed",
        ),
        sa.CheckConstraint("array_length(scopes, 1) >= 1", name="ck_api_keys_scopes_not_empty"),
        sa.CheckConstraint(
            "(previous_secret_hash IS NULL) = (previous_secret_expires_at IS NULL)",
            name="ck_api_keys_previous_secret_paired",
        ),
    )

    op.create_index(
        "uq_api_keys_organization_id_name_active",
        "api_keys",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("deactivated_at IS NULL"),
    )
    op.create_index("ix_api_keys_organization_id_deactivated_at", "api_keys", ["organization_id", "deactivated_at"])
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    # 2. Add api_key_id to audit_logs
    op.add_column(
        "audit_logs",
        sa.Column("api_key_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_logs_api_key_id",
        "audit_logs",
        "api_keys",
        ["api_key_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 3. Add non-blocking CHECK constraint
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_actor_xor_api_key "
        "CHECK (actor_id IS NULL OR api_key_id IS NULL) NOT VALID"
    )
    op.execute("ALTER TABLE audit_logs VALIDATE CONSTRAINT ck_audit_logs_actor_xor_api_key")

    # 4. Create partial index concurrently
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_audit_logs_organization_id_api_key_id",
            "audit_logs",
            ["organization_id", "api_key_id"],
            postgresql_where=sa.text("api_key_id IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_audit_logs_organization_id_api_key_id", table_name="audit_logs", postgresql_concurrently=True)

    op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_actor_xor_api_key")
    op.drop_constraint("fk_audit_logs_api_key_id", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "api_key_id")
    op.drop_table("api_keys")
