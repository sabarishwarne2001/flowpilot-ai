"""ARCH-15 Step 15.1 — stripe_inbound_events (EXPAND)

The inbound door. Nothing else in this phase can be trusted until inbound
events are disciplined, which is why this migration and the endpoint it backs
ship alone, exactly as ARCH-13's 13.1 did: it is the only change in the phase
that alters what a public, unauthenticated endpoint accepts.

Revision ID: arch15_step1_stripe_inbound_events
Revises: arch13_step7_verification
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch15_step1_stripe_inbound_events"
down_revision = "arch13_step7_verification"
branch_labels = None
depends_on = None


STATUS_ENUM_NAME = "stripe_inbound_status"

STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "CLAIMED",
    "PROCESSED",
    "IGNORED",
    "FAILED",
    "DEAD",
)


def _claimable_predicate() -> str:
    return (
        f"status IN ('PENDING'::{STATUS_ENUM_NAME}, 'FAILED'::{STATUS_ENUM_NAME})"
    )


def upgrade() -> None:
    # `create_type=False` on the model side means the type is this migration's
    # responsibility, and only this migration's. Created explicitly rather
    # than as a side effect of the column so a failed table create does not
    # leave a half-owned type behind.
    postgresql.ENUM(*STATUS_VALUES, name=STATUS_ENUM_NAME).create(
        op.get_bind(), checkfirst=True
    )

    op.create_table(
        "stripe_inbound_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "seq",
            sa.BigInteger(),
            sa.Identity(always=False, start=1),
            nullable=False,
        ),
        # ---- identity, as Stripe states it ------------------------------
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("api_version", sa.String(length=64), nullable=True),
        sa.Column("stripe_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        # ---- the artifact a Stripe support ticket asks for --------------
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("signature_header", sa.Text(), nullable=False),
        # ---- tenancy, discovered from the payload -----------------------
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        # ---- queue discipline -------------------------------------------
        sa.Column(
            "status",
            postgresql.ENUM(
                *STATUS_VALUES, name=STATUS_ENUM_NAME, create_type=False
            ),
            nullable=False,
            server_default=sa.text(f"'PENDING'::{STATUS_ENUM_NAME}"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_stripe_inbound_events_organization_id_organizations",
            ondelete="SET NULL",
        ),
        # A10. Replay protection is a UNIQUE index, not a check in code.
        sa.UniqueConstraint(
            "stripe_event_id", name="uq_stripe_inbound_events_event_id"
        ),
        sa.UniqueConstraint("seq", name="uq_stripe_inbound_events_seq"),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_stripe_inbound_events_attempts_non_negative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_stripe_inbound_events_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "length(stripe_event_id) > 0",
            name="ck_stripe_inbound_events_event_id_not_blank",
        ),
        sa.CheckConstraint(
            "length(event_type) > 0",
            name="ck_stripe_inbound_events_event_type_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_stripe_inbound_events_payload_is_object",
        ),
        sa.CheckConstraint(
            f"(status = 'CLAIMED'::{STATUS_ENUM_NAME}) "
            "= (claim_expires_at IS NOT NULL)",
            name="ck_stripe_inbound_events_lease_matches_status",
        ),
        sa.CheckConstraint(
            f"(status IN ('PROCESSED'::{STATUS_ENUM_NAME}, "
            f"'IGNORED'::{STATUS_ENUM_NAME}, 'DEAD'::{STATUS_ENUM_NAME})) "
            "= (processed_at IS NOT NULL)",
            name="ck_stripe_inbound_events_processed_matches_status",
        ),
        # Belt and braces for the livemode guard the endpoint already
        # applies. `current_setting(..., true)` yields NULL when the GUC is
        # unset, so a deployment that has not opted in is unaffected. Set it
        # per-connection to arm this:
        #     ALTER DATABASE flowpilot SET app.stripe_livemode = 'true';
        sa.CheckConstraint(
            "livemode = (current_setting('app.stripe_livemode', true) = 'true') "
            "OR current_setting('app.stripe_livemode', true) IS NULL",
            name="ck_stripe_inbound_events_livemode_matches_env",
        ),
    )

    # Claim ordering is (available_at, seq): that is the ORDER BY inside
    # `claim_eligible_rows`. An index on (available_at, received_at) — as the
    # phase plan sketched — would simply never be read.
    op.execute(
        "CREATE INDEX ix_stripe_inbound_events_claimable "
        "ON stripe_inbound_events (available_at, seq) "
        f"WHERE {_claimable_predicate()}"
    )
    op.execute(
        "CREATE INDEX ix_stripe_inbound_events_expired_leases "
        "ON stripe_inbound_events (claim_expires_at) "
        f"WHERE status = 'CLAIMED'::{STATUS_ENUM_NAME}"
    )
    op.execute(
        "CREATE INDEX ix_stripe_inbound_events_org_received "
        "ON stripe_inbound_events (organization_id, received_at DESC) "
        "WHERE organization_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_stripe_inbound_events_type_received "
        "ON stripe_inbound_events (event_type, received_at DESC)"
    )
    # An inbound dead letter means *we* have a bug and billing state was not
    # applied. It gets its own index because it gets its own alert and its
    # own runbook — unlike an outbound dead letter, which is a customer's
    # endpoint being down.
    op.execute(
        "CREATE INDEX ix_stripe_inbound_events_dead "
        "ON stripe_inbound_events (received_at DESC) "
        f"WHERE status = 'DEAD'::{STATUS_ENUM_NAME}"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_stripe_inbound_events_dead")
    op.execute("DROP INDEX IF EXISTS ix_stripe_inbound_events_type_received")
    op.execute("DROP INDEX IF EXISTS ix_stripe_inbound_events_org_received")
    op.execute("DROP INDEX IF EXISTS ix_stripe_inbound_events_expired_leases")
    op.execute("DROP INDEX IF EXISTS ix_stripe_inbound_events_claimable")
    op.drop_table("stripe_inbound_events")
    postgresql.ENUM(name=STATUS_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
