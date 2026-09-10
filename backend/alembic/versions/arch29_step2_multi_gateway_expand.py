"""ARCH-29 Tranche 3 Step 1 (EXPAND) — gateway-neutral billing identifiers.

Revision ID: arch29_step2_multi_gateway_expand
Revises: arch29_step1_quota_tier_commercials
Create Date: 2026-09-10

D-3, approved: Dodo Payments as Merchant of Record, `reconcile_service` extended
downstream.

WHY A MIGRATION IS THE FIRST STEP OF A "JUST WRITE AN ADAPTER" TASK
==================================================================

`stripe_gateway.py` is a genuinely clean seam — it is the only module importing
the SDK and `verify_arch15.py` asserts that statically. That makes the CODE side
of a second gateway tractable. It is not where the coupling lives.

The coupling lives in the schema:

    billing_accounts.stripe_customer_id   String(255)  NOT NULL
        + CHECK length(stripe_customer_id) > 0
    invoices.stripe_invoice_id            unique, indexed
    dunning_actions.stripe_event_id
    stripe_inbound_events                 whole table, with a livemode CHECK
        bound to the `app.stripe_livemode` session setting

A `DodoGateway` returning a customer id of `cus_dodo_...` has nowhere to put it.
`stripe_customer_id` is NOT NULL, so the row cannot be written at all, and no
amount of Protocol design changes that. Writing a Dodo id into a column named
`stripe_customer_id` would "work" and would be the worst outcome: every future
reader, every support query, every reconciliation script would be reading a
column whose name asserts a provenance the data does not have.

WHAT THIS MIGRATION DOES, AND DOES NOT
======================================

EXPAND only. It adds gateway-neutral columns beside the Stripe-named ones,
backfills them, and relaxes the NOT NULL that blocks a second gateway. It does
NOT drop a single column, and it does NOT rename `stripe_inbound_events`.

That sequencing is not caution for its own sake. Between this migration and the
CONTRACT that follows, both column sets are live and agree, so a rollback is a
code deploy rather than a data migration. The CONTRACT step — dropping
`stripe_*` and renaming the inbound table — happens only once
`verify_arch29_tranche3.py` G2 reports zero readers of the old names.

THE `gateway` DISCRIMINATOR IS NOT DECORATION
=============================================

Without it, `gateway_customer_id = 'cus_123'` is ambiguous: Stripe and Dodo both
issue opaque strings and nothing distinguishes them. A reconciliation job that
fetched a Dodo customer against the Stripe API would get a 404 and, depending on
the caller, either log a warning or create a duplicate. The discriminator makes
"which vendor issued this identifier" a column rather than an inference from
deployment configuration that was true when the row was written.

It is NOT NULL with a server default of 'STRIPE'. Every existing row was written
by the Stripe integration, so the default is a statement of fact rather than a
guess.

WHY THE LIVEMODE CHECK IS GENERALISED RATHER THAN DROPPED
==========================================================

`ck_stripe_inbound_events_livemode_matches_env` asserts that a webhook's
`livemode` flag matches `current_setting('app.stripe_livemode')`. It exists to
stop a test-mode event from being processed by a production deployment — the
class of accident that ends with a real subscription cancelled by a sandbox
webhook.

Dodo has the same hazard: its CLI listener and Testing tab deliver test-mode
events signed with a real secret. Dropping the constraint while adding a second
gateway would remove the guard exactly when the number of ways to trip it
doubles. The replacement reads `app.billing_livemode`, with
`app.stripe_livemode` retained as a fallback so a deployment that has not yet
updated its session-setting wiring keeps the old behaviour instead of silently
losing the check.

NOTE ON `invoices` UNDER A MERCHANT OF RECORD
=============================================

Under an MoR, Dodo is the legal seller. Its invoice is the tax document; the row
in `invoices` becomes an internal statement of account — still authoritative for
attribution, no longer authoritative as a tax artifact. The frozen SHA-256
digests keep their meaning (they prove what we asserted, and when).

That is a documentation and reconciliation concern rather than a schema one, so
this migration only renames the identifier column. The downstream half of
`reconcile_service` is Tranche 3 Step 4.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch29_step2_multi_gateway_expand"
down_revision = "arch29_step1_quota_tier_commercials"
branch_labels = None
depends_on = None


#: Vocabulary for the discriminator. Expanded in its own autocommit-safe step if
#: a third gateway ever lands; a CHECK rather than a PG ENUM specifically so
#: that expansion is an ALTER on a constraint rather than an enum mutation
#: inside a transaction block.
KNOWN_GATEWAYS = ("STRIPE", "DODO")

_GATEWAY_IN = ", ".join(f"'{g}'" for g in KNOWN_GATEWAYS)


LIVEMODE_CHECK_V2 = (
    "livemode = (coalesce("
    "current_setting('app.billing_livemode', true), "
    "current_setting('app.stripe_livemode', true)"
    ") = 'true') "
    "OR coalesce("
    "current_setting('app.billing_livemode', true), "
    "current_setting('app.stripe_livemode', true)"
    ") IS NULL"
)

LIVEMODE_CHECK_V1 = (
    "livemode = (current_setting('app.stripe_livemode', true) = 'true') "
    "OR current_setting('app.stripe_livemode', true) IS NULL"
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # billing_accounts
    # ------------------------------------------------------------------
    op.add_column(
        "billing_accounts",
        sa.Column(
            "gateway",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'STRIPE'"),
        ),
    )
    op.add_column(
        "billing_accounts",
        sa.Column("gateway_customer_id", sa.String(length=255), nullable=True),
    )

    # Backfill before constraining. Every existing row came from Stripe.
    op.execute(
        "UPDATE billing_accounts "
        "SET gateway_customer_id = stripe_customer_id "
        "WHERE gateway_customer_id IS NULL"
    )

    op.create_check_constraint(
        "ck_billing_accounts_gateway_known",
        "billing_accounts",
        f"gateway IN ({_GATEWAY_IN})",
    )
    op.create_check_constraint(
        "ck_billing_accounts_gateway_customer_id_not_blank",
        "billing_accounts",
        "gateway_customer_id IS NULL OR length(gateway_customer_id) > 0",
    )

    # One customer id per gateway. Scoped BY GATEWAY, because two vendors
    # issuing the same opaque string is not a collision — it is two unrelated
    # customers, and a global unique index would refuse the second one at
    # random depending on which signed up first.
    op.create_index(
        "uq_billing_accounts_gateway_customer",
        "billing_accounts",
        ["gateway", "gateway_customer_id"],
        unique=True,
        postgresql_where=sa.text("gateway_customer_id IS NOT NULL"),
    )

    # THE line that unblocks a second gateway. Until this runs, a DodoGateway
    # cannot write a billing account at all.
    op.alter_column(
        "billing_accounts",
        "stripe_customer_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # The old CHECK asserted length > 0 on a column that is now nullable.
    # Postgres CHECKs pass on NULL, so this is technically still satisfiable —
    # but restate it explicitly rather than relying on three-valued logic being
    # remembered correctly by the next reader.
    op.drop_constraint(
        "ck_billing_accounts_customer_id_not_blank",
        "billing_accounts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_billing_accounts_customer_id_not_blank",
        "billing_accounts",
        "stripe_customer_id IS NULL OR length(stripe_customer_id) > 0",
    )

    # A Stripe account must still carry a Stripe id. Relaxing NOT NULL for
    # Dodo's benefit must not silently permit a Stripe row with no customer.
    op.create_check_constraint(
        "ck_billing_accounts_stripe_requires_customer",
        "billing_accounts",
        "gateway <> 'STRIPE' OR stripe_customer_id IS NOT NULL",
    )

    # ------------------------------------------------------------------
    # invoices
    # ------------------------------------------------------------------
    op.add_column(
        "invoices",
        sa.Column(
            "gateway",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'STRIPE'"),
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("gateway_invoice_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE invoices "
        "SET gateway_invoice_id = stripe_invoice_id "
        "WHERE gateway_invoice_id IS NULL"
    )
    op.create_check_constraint(
        "ck_invoices_gateway_known",
        "invoices",
        f"gateway IN ({_GATEWAY_IN})",
    )
    op.create_index(
        "uq_invoices_gateway_invoice",
        "invoices",
        ["gateway", "gateway_invoice_id"],
        unique=True,
        postgresql_where=sa.text("gateway_invoice_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # stripe_inbound_events  (renamed in CONTRACT, not here)
    # ------------------------------------------------------------------
    op.add_column(
        "stripe_inbound_events",
        sa.Column(
            "gateway",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'STRIPE'"),
        ),
    )
    op.add_column(
        "stripe_inbound_events",
        sa.Column("gateway_event_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE stripe_inbound_events "
        "SET gateway_event_id = stripe_event_id "
        "WHERE gateway_event_id IS NULL"
    )
    op.create_check_constraint(
        "ck_stripe_inbound_events_gateway_known",
        "stripe_inbound_events",
        f"gateway IN ({_GATEWAY_IN})",
    )

    # Idempotency, per gateway.
    #
    # This index is the replay defence. Dodo's `webhook-id` header is its
    # idempotency key and its retry schedule runs to 8 attempts over ~28 hours,
    # so duplicate delivery is routine rather than exceptional. Scoping by
    # gateway keeps a Stripe `evt_` and a Dodo UUID from ever being compared.
    op.create_index(
        "uq_inbound_events_gateway_event",
        "stripe_inbound_events",
        ["gateway", "gateway_event_id"],
        unique=True,
        postgresql_where=sa.text("gateway_event_id IS NOT NULL"),
    )

    op.drop_constraint(
        "ck_stripe_inbound_events_livemode_matches_env",
        "stripe_inbound_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_stripe_inbound_events_livemode_matches_env",
        "stripe_inbound_events",
        LIVEMODE_CHECK_V2,
    )

    # ------------------------------------------------------------------
    # dunning_actions
    # ------------------------------------------------------------------
    op.add_column(
        "dunning_actions",
        sa.Column("gateway_event_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE dunning_actions "
        "SET gateway_event_id = stripe_event_id "
        "WHERE gateway_event_id IS NULL AND stripe_event_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("dunning_actions", "gateway_event_id")

    op.drop_constraint(
        "ck_stripe_inbound_events_livemode_matches_env",
        "stripe_inbound_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_stripe_inbound_events_livemode_matches_env",
        "stripe_inbound_events",
        LIVEMODE_CHECK_V1,
    )
    op.drop_index(
        "uq_inbound_events_gateway_event", table_name="stripe_inbound_events"
    )
    op.drop_constraint(
        "ck_stripe_inbound_events_gateway_known",
        "stripe_inbound_events",
        type_="check",
    )
    op.drop_column("stripe_inbound_events", "gateway_event_id")
    op.drop_column("stripe_inbound_events", "gateway")

    op.drop_index("uq_invoices_gateway_invoice", table_name="invoices")
    op.drop_constraint("ck_invoices_gateway_known", "invoices", type_="check")
    op.drop_column("invoices", "gateway_invoice_id")
    op.drop_column("invoices", "gateway")

    # Restoring NOT NULL requires every row to carry a Stripe id. A Dodo-only
    # account cannot satisfy that, so the downgrade refuses rather than
    # deleting rows to make room for itself.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM billing_accounts "
        "           WHERE stripe_customer_id IS NULL) THEN "
        "  RAISE EXCEPTION 'Cannot downgrade: billing_accounts rows exist "
        "with no stripe_customer_id. These were created by a non-Stripe "
        "gateway and have no Stripe identity to restore.' "
        "  USING ERRCODE = '23502'; "
        "END IF; END $$;"
    )

    op.drop_constraint(
        "ck_billing_accounts_stripe_requires_customer",
        "billing_accounts",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_accounts_customer_id_not_blank",
        "billing_accounts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_billing_accounts_customer_id_not_blank",
        "billing_accounts",
        "length(stripe_customer_id) > 0",
    )
    op.alter_column(
        "billing_accounts",
        "stripe_customer_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_index(
        "uq_billing_accounts_gateway_customer", table_name="billing_accounts"
    )
    op.drop_constraint(
        "ck_billing_accounts_gateway_customer_id_not_blank",
        "billing_accounts",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_accounts_gateway_known", "billing_accounts", type_="check"
    )
    op.drop_column("billing_accounts", "gateway_customer_id")
    op.drop_column("billing_accounts", "gateway")