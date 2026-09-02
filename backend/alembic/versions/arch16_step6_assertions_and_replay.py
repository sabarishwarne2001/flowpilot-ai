"""ARCH-16 Step 6 — assertion replay guard, consumed assertions, auth requests.

Revision ID: arch16_step6_assertions_and_replay
Revises: arch16_step5_sessions_and_policy
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

TBL_SESSIONS = "sessions"

revision = "arch16_step6_assertions_and_replay"
down_revision = "arch16_step5_sessions_and_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saml_assertion_replay_guard",
        sa.Column("assertion_id", sa.Text(), primary_key=True),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("not_on_or_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_replay_guard_sweep", "saml_assertion_replay_guard",
                    ["not_on_or_after"])

    op.create_table(
        "sso_auth_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text()),
        sa.Column("code_verifier_encrypted", sa.LargeBinary()),
        sa.Column("relay_state", sa.Text()),
        sa.Column("redirect_path", sa.Text()),
        sa.Column("force_authn", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_ip", postgresql.INET()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("request_id", name="uq_sso_auth_request_id"),
        sa.CheckConstraint("protocol IN ('SAML2','OIDC')",
                           name="ck_sso_auth_request_protocol"),
        sa.CheckConstraint("expires_at > created_at",
                           name="ck_sso_auth_request_ordered"),
    )
    op.create_index("ix_sso_auth_requests_sweep", "sso_auth_requests",
                    ["expires_at"], postgresql_where=sa.text("consumed_at IS NULL"))

    op.create_table(
        "sso_assertions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idp_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("session_id", postgresql.UUID(as_uuid=True)),

        sa.Column("raw_payload", sa.LargeBinary()),
        sa.Column("raw_purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_digest", sa.CHAR(71), nullable=False),

        sa.Column("authn_instant", sa.DateTime(timezone=True)),
        sa.Column("session_index", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("consumed_attributes", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_ip", postgresql.INET()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["idp_config_id"], ["enterprise_idp_configs.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], [f"{TBL_SESSIONS}.id"],
                                ondelete="SET NULL"),
        sa.CheckConstraint(
            "outcome IN ('ACCEPTED','REJECTED_SIGNATURE','REJECTED_AUDIENCE',"
            "'REJECTED_DESTINATION','REJECTED_EXPIRED','REJECTED_REPLAY',"
            "'REJECTED_NO_AUTHN_INSTANT','REJECTED_UNSOLICITED',"
            "'REJECTED_DOMAIN','REJECTED_SEAT_CAP','REJECTED_UNKNOWN')",
            name="ck_sso_assertion_outcome"),
        sa.CheckConstraint("payload_digest LIKE 'sha256:%'",
                           name="ck_sso_assertion_digest_prefixed"),
    )
    op.create_index("ix_sso_assertions_org", "sso_assertions",
                    ["organization_id", "created_at"])
    op.create_index("ix_sso_assertions_purge", "sso_assertions",
                    ["raw_purge_after"],
                    postgresql_where=sa.text("raw_payload IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_sso_assertions_purge", table_name="sso_assertions")
    op.drop_index("ix_sso_assertions_org", table_name="sso_assertions")
    op.drop_table("sso_assertions")
    op.drop_index("ix_sso_auth_requests_sweep", table_name="sso_auth_requests")
    op.drop_table("sso_auth_requests")
    op.drop_index("ix_replay_guard_sweep", table_name="saml_assertion_replay_guard")
    op.drop_table("saml_assertion_replay_guard")
