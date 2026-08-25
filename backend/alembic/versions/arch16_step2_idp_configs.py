"""ARCH-16 Step 2 — enterprise_idp_configs, signing certificates, role mappings.

Revision ID: arch16_step2_idp_configs
Revises: arch16_step1_verified_domains
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "arch16_step2_idp_configs"
down_revision = "arch16_step1_verified_domains"
branch_labels = None
depends_on = None

ORG_ROLE_VALUES = ("OWNER", "ADMIN", "BILLING", "MEMBER")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("SAML2", "OIDC", name="idp_protocol").create(bind, checkfirst=True)
    postgresql.ENUM("OPEN", "CAPPED", "INVITE_ONLY",
                    name="jit_provisioning_mode").create(bind, checkfirst=True)

    idp_protocol = postgresql.ENUM(name="idp_protocol", create_type=False)
    jit_mode = postgresql.ENUM(name="jit_provisioning_mode", create_type=False)
    org_role = postgresql.ENUM(*ORG_ROLE_VALUES, name="organization_role",
                               create_type=False)

    op.create_table(
        "enterprise_idp_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verified_domain_id", postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("protocol", idp_protocol, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),

        # --- SAML ---
        sa.Column("idp_entity_id", sa.Text()),
        sa.Column("idp_sso_url", sa.Text()),
        sa.Column("idp_slo_url", sa.Text()),
        sa.Column("metadata_url", sa.Text()),
        sa.Column("metadata_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("want_assertions_signed", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("want_response_signed", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("want_assertions_encrypted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("allow_unsolicited", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("name_id_format", sa.Text()),

        # --- OIDC ---
        sa.Column("oidc_issuer", sa.Text()),
        sa.Column("oidc_client_id", sa.Text()),
        sa.Column("oidc_client_secret_encrypted", sa.LargeBinary()),
        sa.Column("oidc_discovery_url", sa.Text()),
        sa.Column("oidc_jwks_json", postgresql.JSONB()),
        sa.Column("oidc_jwks_cached_at", sa.DateTime(timezone=True)),
        sa.Column("oidc_authorization_endpoint", sa.Text()),
        sa.Column("oidc_token_endpoint", sa.Text()),
        sa.Column("oidc_jwks_uri", sa.Text()),

        # --- JIT / Seats ---
        sa.Column("jit_provisioning_mode", jit_mode, nullable=False,
                  server_default="CAPPED"),
        sa.Column("jit_default_org_role", org_role, nullable=False,
                  server_default="MEMBER"),
        sa.Column("jit_seat_cap", sa.Integer()),

        sa.Column("force_reauth_max_age_s", sa.Integer()),

        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verified_domain_id"], ["verified_domains.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                ondelete="SET NULL"),

        sa.CheckConstraint(
            "jit_provisioning_mode <> 'CAPPED' OR jit_seat_cap IS NOT NULL",
            name="ck_idp_capped_has_cap"),
        sa.CheckConstraint("jit_seat_cap IS NULL OR jit_seat_cap >= 0",
                           name="ck_idp_seat_cap_nonneg"),
        sa.CheckConstraint("jit_default_org_role <> 'OWNER'",
                           name="ck_idp_jit_role_not_owner"),
        sa.CheckConstraint(
            "protocol <> 'SAML2' OR (idp_entity_id IS NOT NULL AND idp_sso_url IS NOT NULL)",
            name="ck_idp_saml_fields"),
        sa.CheckConstraint(
            "protocol <> 'OIDC' OR (oidc_issuer IS NOT NULL AND oidc_client_id IS NOT NULL)",
            name="ck_idp_oidc_fields"),
    )

    op.create_index("uq_idp_active_per_org", "enterprise_idp_configs",
                    ["organization_id"], unique=True,
                    postgresql_where=sa.text("is_active"))
    op.create_index("ix_idp_configs_org", "enterprise_idp_configs",
                    ["organization_id"])
    op.create_index("ix_idp_configs_entity", "enterprise_idp_configs",
                    ["idp_entity_id"],
                    postgresql_where=sa.text("idp_entity_id IS NOT NULL"))

    op.create_table(
        "idp_signing_certificates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("private_key_encrypted", sa.LargeBinary()),
        sa.Column("fingerprint_sha256", sa.CHAR(64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column("not_after", sa.DateTime(timezone=True)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("idp_config_id", "side", "fingerprint_sha256",
                            name="uq_idp_cert_fingerprint"),
        sa.CheckConstraint("side IN ('IDP','SP')", name="ck_idp_cert_side"),
        sa.CheckConstraint("side = 'SP' OR private_key_encrypted IS NULL",
                           name="ck_idp_cert_idp_has_no_key"),
    )
    op.create_index("uq_idp_cert_primary", "idp_signing_certificates",
                    ["idp_config_id", "side"], unique=True,
                    postgresql_where=sa.text("is_primary"))
    op.create_index("ix_idp_cert_live", "idp_signing_certificates",
                    ["idp_config_id", "side"],
                    postgresql_where=sa.text("retired_at IS NULL"))

    op.create_table(
        "idp_role_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attribute_name", sa.Text(), nullable=False),
        sa.Column("match_kind", sa.Text(), nullable=False),
        sa.Column("match_value", sa.Text(), nullable=False),
        sa.Column("organization_role", org_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("idp_config_id", "priority",
                            name="uq_idp_role_mapping_priority"),
        sa.CheckConstraint("organization_role <> 'OWNER'",
                           name="ck_idp_role_mapping_not_owner"),
        sa.CheckConstraint("match_kind IN ('EQUALS','CONTAINS','PREFIX')",
                           name="ck_idp_role_mapping_kind"),
    )


def downgrade() -> None:
    op.drop_table("idp_role_mappings")
    op.drop_index("ix_idp_cert_live", table_name="idp_signing_certificates")
    op.drop_index("uq_idp_cert_primary", table_name="idp_signing_certificates")
    op.drop_table("idp_signing_certificates")
    op.drop_index("ix_idp_configs_entity", table_name="enterprise_idp_configs")
    op.drop_index("ix_idp_configs_org", table_name="enterprise_idp_configs")
    op.drop_index("uq_idp_active_per_org", table_name="enterprise_idp_configs")
    op.drop_table("enterprise_idp_configs")
    bind = op.get_bind()
    postgresql.ENUM(name="jit_provisioning_mode").drop(bind, checkfirst=True)
    postgresql.ENUM(name="idp_protocol").drop(bind, checkfirst=True)