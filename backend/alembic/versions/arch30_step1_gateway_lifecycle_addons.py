"""ARCH-30 Tranche 2 Step 1 — gateway lifecycle columns and the add-on ledger.

Revision ID: arch30_step1_gateway_lifecycle_addons
Revises: arch29_step2_multi_gateway_expand
Create Date: 2026-09-11

Closes the schema half of four findings from the Tranche 2 ground-truth audit.

T4-F2b — `stripe_inbound_events.stripe_event_id` IS STILL NOT NULL
==================================================================

`arch29_step2` added `gateway` and `gateway_event_id` and documented, in
`inbound_service.persist_gateway_event`, that a Dodo event "writes NULL there,
which the column already permits". It does not. `arch15_step1` created the
column NOT NULL and no migration since has relaxed it, so every Dodo insert
failed a NOT NULL check that the docstring said did not exist.

The relaxation is paired with the invariant that made NOT NULL correct in the
first place, restated per gateway:

    ck_stripe_inbound_events_stripe_requires_event_id
        gateway <> 'STRIPE' OR stripe_event_id IS NOT NULL
    ck_stripe_inbound_events_gateway_event_id_present
        gateway = 'STRIPE' OR gateway_event_id IS NOT NULL

The second is the one that matters for Dodo. `uq_inbound_events_gateway_event`
is PARTIAL on `gateway_event_id IS NOT NULL`, so a non-Stripe row without the
id would sit outside the replay index entirely: every retry of that delivery
would insert again.

SUBSCRIPTIONS LEARN WHICH VENDOR ISSUED THEIR IDENTIFIER
========================================================

`subscriptions.stripe_subscription_id` was NOT NULL with a full unique index.
A Dodo subscription id written there would "work" and would be the outcome
`arch29_step2` spent a docstring refusing for `billing_accounts`: a column
whose name asserts a provenance the data does not have. The same EXPAND shape
is applied — `gateway`, `gateway_subscription_id`, backfill, relax, and a
CHECK that keeps a Stripe row honest.

`grace_ends_at` (D-11) is the end of full access after a renewal failure.
Dodo exposes no `past_due` state and no grace timestamp: a failed renewal moves
the subscription straight to `on_hold` and its Payment Retries run inside a
recovery window (13 days by default). The grace end is therefore OURS, stamped
once on the first observed `on_hold`, and the column says so by existing here
rather than being read from a payload field that is not there.

THE ADD-ON LEDGER (D-6, D-8)
============================

`organization_addons` is one row per (organization, add-on). It records two
different facts and keeps them in different columns on purpose:

    purchase_status   what the gateway says about a purchased add-on
    status            what this platform currently grants, after tier
                      bundling and the D-6 downgrade grace are applied

Folding them together is how a customer who is still entitled through their
tier gets a "your add-on ended" email when they cancel a redundant purchase.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch30_step1_gateway_lifecycle_addons"
down_revision = "arch29_step2_multi_gateway_expand"
branch_labels = None
depends_on = None


KNOWN_GATEWAYS = ("STRIPE", "DODO")
_GATEWAY_IN = ", ".join(f"'{g}'" for g in KNOWN_GATEWAYS)

ADDON_KEYS = ("addon.custom_domain", "addon.warehouse_sync")
_ADDON_IN = ", ".join(f"'{k}'" for k in ADDON_KEYS)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # stripe_inbound_events (T4-F2b)
    # ------------------------------------------------------------------
    op.alter_column(
        "stripe_inbound_events",
        "stripe_event_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_stripe_inbound_events_stripe_requires_event_id",
        "stripe_inbound_events",
        "gateway <> 'STRIPE' OR stripe_event_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_stripe_inbound_events_gateway_event_id_present",
        "stripe_inbound_events",
        "gateway = 'STRIPE' OR gateway_event_id IS NOT NULL",
    )

    # ------------------------------------------------------------------
    # subscriptions
    # ------------------------------------------------------------------
    op.add_column(
        "subscriptions",
        sa.Column(
            "gateway",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'STRIPE'"),
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("gateway_subscription_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE subscriptions SET gateway_subscription_id = stripe_subscription_id "
        "WHERE gateway_subscription_id IS NULL"
    )
    op.create_check_constraint(
        "ck_subscriptions_gateway_known",
        "subscriptions",
        f"gateway IN ({_GATEWAY_IN})",
    )
    op.create_index(
        "uq_subscriptions_gateway_subscription",
        "subscriptions",
        ["gateway", "gateway_subscription_id"],
        unique=True,
        postgresql_where=sa.text("gateway_subscription_id IS NOT NULL"),
    )
    op.alter_column(
        "subscriptions",
        "stripe_subscription_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_subscriptions_stripe_requires_subscription_id",
        "subscriptions",
        "gateway <> 'STRIPE' OR stripe_subscription_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_subscriptions_gateway_subscription_id_present",
        "subscriptions",
        "gateway = 'STRIPE' OR gateway_subscription_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_subscriptions_grace_only_when_delinquent",
        "subscriptions",
        "grace_ends_at IS NULL OR status IN "
        "('past_due'::subscription_status, 'unpaid'::subscription_status)",
    )

    # ------------------------------------------------------------------
    # organization_addons (D-6, D-8)
    # ------------------------------------------------------------------
    op.create_table(
        "organization_addons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("addon_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column("granted_by", sa.String(length=16), nullable=True),
        sa.Column(
            "purchase_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
        sa.Column(
            "purchase_grace_ends_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("gateway", sa.String(length=16), nullable=True),
        sa.Column("gateway_subscription_id", sa.String(length=255), nullable=True),
        sa.Column(
            "gateway_state_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lapsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("halted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_stage", sa.String(length=24), nullable=True),
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
        sa.CheckConstraint(
            f"addon_key IN ({_ADDON_IN})", name="ck_organization_addons_key_known"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'GRACE', 'LAPSED')",
            name="ck_organization_addons_status_known",
        ),
        sa.CheckConstraint(
            "granted_by IS NULL OR granted_by IN ('TIER', 'SUBSCRIPTION')",
            name="ck_organization_addons_granted_by_known",
        ),
        sa.CheckConstraint(
            "purchase_status IN ('NONE', 'ACTIVE', 'ON_HOLD', 'ENDED')",
            name="ck_organization_addons_purchase_status_known",
        ),
        sa.CheckConstraint(
            f"gateway IS NULL OR gateway IN ({_GATEWAY_IN})",
            name="ck_organization_addons_gateway_known",
        ),
        sa.CheckConstraint(
            "status <> 'GRACE' OR (grace_started_at IS NOT NULL "
            "AND grace_ends_at IS NOT NULL AND grace_ends_at > grace_started_at)",
            name="ck_organization_addons_grace_has_window",
        ),
        sa.CheckConstraint(
            "status <> 'LAPSED' OR lapsed_at IS NOT NULL",
            name="ck_organization_addons_lapsed_has_timestamp",
        ),
        sa.CheckConstraint(
            "purchase_status <> 'ON_HOLD' OR purchase_grace_ends_at IS NOT NULL",
            name="ck_organization_addons_on_hold_has_grace",
        ),
        sa.CheckConstraint(
            "purchase_status = 'NONE' OR (gateway IS NOT NULL "
            "AND gateway_subscription_id IS NOT NULL)",
            name="ck_organization_addons_purchase_names_subscription",
        ),
        sa.CheckConstraint(
            "gateway_state_version >= 0",
            name="ck_organization_addons_state_version_non_negative",
        ),
    )
    op.create_index(
        "uq_organization_addons_org_key",
        "organization_addons",
        ["organization_id", "addon_key"],
        unique=True,
    )
    op.create_index(
        "uq_organization_addons_gateway_subscription",
        "organization_addons",
        ["gateway", "gateway_subscription_id"],
        unique=True,
        postgresql_where=sa.text("gateway_subscription_id IS NOT NULL"),
    )
    op.create_index(
        "ix_organization_addons_grace_due",
        "organization_addons",
        ["grace_ends_at"],
        postgresql_where=sa.text("status = 'GRACE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_addons_grace_due", table_name="organization_addons"
    )
    op.drop_index(
        "uq_organization_addons_gateway_subscription",
        table_name="organization_addons",
    )
    op.drop_index("uq_organization_addons_org_key", table_name="organization_addons")
    op.drop_table("organization_addons")

    # A non-Stripe subscription has no Stripe identity to restore. Refuse
    # rather than delete rows to make room for the old NOT NULL.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM subscriptions WHERE stripe_subscription_id IS NULL) "
        "OR EXISTS (SELECT 1 FROM stripe_inbound_events WHERE stripe_event_id IS NULL) THEN "
        "  RAISE EXCEPTION 'Cannot downgrade: non-Stripe subscriptions or inbound "
        "events exist and have no Stripe identifier to restore.' "
        "  USING ERRCODE = '23502'; "
        "END IF; END $$;"
    )

    op.drop_constraint(
        "ck_subscriptions_grace_only_when_delinquent", "subscriptions", type_="check"
    )
    op.drop_constraint(
        "ck_subscriptions_gateway_subscription_id_present",
        "subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "ck_subscriptions_stripe_requires_subscription_id",
        "subscriptions",
        type_="check",
    )
    op.alter_column(
        "subscriptions",
        "stripe_subscription_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_index("uq_subscriptions_gateway_subscription", table_name="subscriptions")
    op.drop_constraint("ck_subscriptions_gateway_known", "subscriptions", type_="check")
    op.drop_column("subscriptions", "grace_ends_at")
    op.drop_column("subscriptions", "gateway_subscription_id")
    op.drop_column("subscriptions", "gateway")

    op.drop_constraint(
        "ck_stripe_inbound_events_gateway_event_id_present",
        "stripe_inbound_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_stripe_inbound_events_stripe_requires_event_id",
        "stripe_inbound_events",
        type_="check",
    )
    op.alter_column(
        "stripe_inbound_events",
        "stripe_event_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
