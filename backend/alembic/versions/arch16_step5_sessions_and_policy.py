"""ARCH-16 Step 5 — session SSO columns and tenant_security_policies.

Revision ID: arch16_step5_sessions_and_policy
Revises: arch16_step4_scim_keys
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

TBL_SESSIONS = "sessions"

revision = "arch16_step5_sessions_and_policy"
down_revision = "arch16_step4_scim_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("PASSWORD", "SAML2", "OIDC",
                    name="auth_method").create(bind, checkfirst=True)
    postgresql.ENUM("OFF", "PREFIX", "STRICT",
                    name="ip_pinning_mode").create(bind, checkfirst=True)

    auth_method = postgresql.ENUM(name="auth_method", create_type=False)
    ip_pinning = postgresql.ENUM(name="ip_pinning_mode", create_type=False)

    op.add_column(TBL_SESSIONS,
                  sa.Column("auth_method", auth_method, nullable=False,
                            server_default="PASSWORD"))
    op.add_column(TBL_SESSIONS,
                  sa.Column("idp_config_id", postgresql.UUID(as_uuid=True)))
    op.add_column(TBL_SESSIONS, sa.Column("idp_session_index", sa.Text()))
    op.add_column(TBL_SESSIONS, sa.Column("pinned_ip", postgresql.INET()))
    op.add_column(TBL_SESSIONS, sa.Column("pinned_ip_prefix", sa.Integer()))

    op.create_foreign_key(
        "fk_sessions_idp_config", TBL_SESSIONS, "enterprise_idp_configs",
        ["idp_config_id"], ["id"], ondelete="SET NULL")
    op.create_check_constraint(
        "ck_sessions_sso_has_config", TBL_SESSIONS,
        "auth_method = 'PASSWORD' OR idp_config_id IS NOT NULL")
    op.create_check_constraint(
        "ck_sessions_pin_pairs", TBL_SESSIONS,
        "(pinned_ip IS NULL) = (pinned_ip_prefix IS NULL)")

    op.create_index("ix_sessions_idp_session_index", TBL_SESSIONS,
                    ["idp_config_id", "idp_session_index"],
                    postgresql_where=sa.text("idp_session_index IS NOT NULL"))

    op.create_table(
        "tenant_security_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("require_sso", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("sso_bypass_for_owners", sa.Boolean(), nullable=False,
                  server_default=sa.true()),

        sa.Column("ip_pinning", ip_pinning, nullable=False, server_default="OFF"),
        sa.Column("ip_prefix_v4", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("ip_prefix_v6", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("ip_allowlist", postgresql.ARRAY(postgresql.CIDR())),

        sa.Column("max_session_age_s", sa.Integer()),
        sa.Column("idp_session_sync", sa.Boolean(), nullable=False,
                  server_default=sa.false()),

        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("organization_id", name="uq_policy_per_org"),
        sa.CheckConstraint("ip_prefix_v4 BETWEEN 8 AND 32",
                           name="ck_policy_v4_prefix"),
        sa.CheckConstraint("ip_prefix_v6 BETWEEN 32 AND 128",
                           name="ck_policy_v6_prefix"),
        sa.CheckConstraint("max_session_age_s IS NULL OR max_session_age_s >= 300",
                           name="ck_policy_session_age_sane"),
    )


def downgrade() -> None:
    op.drop_table("tenant_security_policies")
    op.drop_index("ix_sessions_idp_session_index", table_name=TBL_SESSIONS)
    op.drop_constraint("ck_sessions_pin_pairs", TBL_SESSIONS, type_="check")
    op.drop_constraint("ck_sessions_sso_has_config", TBL_SESSIONS, type_="check")
    op.drop_constraint("fk_sessions_idp_config", TBL_SESSIONS, type_="foreignkey")
    for col in ("pinned_ip_prefix", "pinned_ip", "idp_session_index",
                "idp_config_id", "auth_method"):
        op.drop_column(TBL_SESSIONS, col)
    bind = op.get_bind()
    postgresql.ENUM(name="ip_pinning_mode").drop(bind, checkfirst=True)
    postgresql.ENUM(name="auth_method").drop(bind, checkfirst=True)