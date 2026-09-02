"""ARCH-16 Step 1 — verified_domains.

EXPAND-only.

Revision ID: arch16_step1_verified_domains
Revises: sec1_step1_session_authenticated_at
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "arch16_step1_verified_domains"
down_revision = "sec1_step1_session_authenticated_at"
branch_labels = None
depends_on = None

DOMAIN_STATUS = ("PENDING", "VERIFIED", "GRACE", "LAPSED", "REVOKED")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*DOMAIN_STATUS, name="domain_status").create(bind, checkfirst=True)
    domain_status = postgresql.ENUM(name="domain_status", create_type=False)

    op.create_table(
        "verified_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("status", domain_status, nullable=False, server_default="PENDING"),

        sa.Column("challenge_token", sa.Text(), nullable=False),
        sa.Column("challenge_issued_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("challenge_expires_at", sa.DateTime(timezone=True), nullable=False),

        sa.Column("first_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False,
                  server_default="0"),

        sa.Column("is_sso_binding", sa.Boolean(), nullable=False,
                  server_default=sa.false()),

        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("organization_id", "domain",
                            name="uq_verified_domains_org_domain"),
        sa.CheckConstraint("domain = lower(domain)",
                           name="ck_verified_domains_lower"),
        sa.CheckConstraint("domain !~ '^\\.|\\.$|\\*|\\s'",
                           name="ck_verified_domains_shape"),
        sa.CheckConstraint("length(domain) - length(replace(domain, '.', '')) >= 1",
                           name="ck_verified_domains_two_labels"),
        sa.CheckConstraint("length(domain) BETWEEN 4 AND 253",
                           name="ck_verified_domains_length"),
        sa.CheckConstraint(
            "status <> 'VERIFIED' OR first_verified_at IS NOT NULL",
            name="ck_verified_domains_verified_has_timestamp"),
        sa.CheckConstraint(
            "is_sso_binding = false OR status IN ('VERIFIED', 'GRACE', 'LAPSED')",
            name="ck_verified_domains_sso_requires_proof"),
        sa.CheckConstraint("challenge_expires_at > challenge_issued_at",
                           name="ck_verified_domains_challenge_ordered"),
    )

    op.create_index("uq_domain_sso_binding", "verified_domains", ["domain"],
                    unique=True, postgresql_where=sa.text("is_sso_binding"))
    op.create_index("ix_verified_domains_recheck", "verified_domains",
                    ["last_checked_at"],
                    postgresql_where=sa.text("status IN ('VERIFIED','GRACE')"))
    op.create_index("ix_verified_domains_org", "verified_domains",
                    ["organization_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_verified_domains_org", table_name="verified_domains")
    op.drop_index("ix_verified_domains_recheck", table_name="verified_domains")
    op.drop_index("uq_domain_sso_binding", table_name="verified_domains")
    op.drop_table("verified_domains")
    postgresql.ENUM(name="domain_status").drop(op.get_bind(), checkfirst=True)
