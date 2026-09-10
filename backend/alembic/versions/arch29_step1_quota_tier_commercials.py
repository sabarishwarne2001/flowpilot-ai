"""ARCH-29 Tranche 2 Step 1 (EXPAND) — commercial columns on quota_tiers.

Revision ID: arch29_step1_quota_tier_commercials
Revises: arch27_step3_revenue_share_ledger
Create Date: 2026-09-10

D-1, approved: a plan's price lives on the tier that grants it.

WHAT THIS CLOSES
================

`list_plans` returned `unit_amount=None, currency=None, interval=None` as
hardcoded literals, so `PlanSelector.formatPrice` fell through to "Contact us
for pricing" on every plan including the self-serve ones. The frontend was
telling the truth: `quota_tiers` had no price columns, so the price genuinely
did not exist anywhere in the schema.

It also closes F-2. `billing.py` resolved `price_id` from the single global
`settings.BILLING_SEAT_PRICE_ID` inside the per-tier loop, so Free, Developer,
Business and Enterprise all carried the same gateway price. Configured, that
charges an Enterprise subscriber the one global amount while granting them
Enterprise entitlements — silent, directional, and discovered only on a
provider statement. `gateway_price_id` is per-tier and per-version.

WHY ALL FOUR COLUMNS OR NONE
============================

`ck_quota_tiers_price_complete` is an all-or-nothing CHECK. A tier with an
amount but no currency is a number nobody can act on; a tier with a currency
and no amount renders as a currency symbol beside nothing. Half-priced rows
are the `COALESCE(cost_basis_micros, 0)` failure in a different table: a
plausible partial value standing in for an absent one.

Enterprise stays all-NULL deliberately. "Contact us for pricing" becomes an
explicit product decision — a published tier that declines to name a number —
rather than the artifact of a missing column, and `formatPrice` renders the
same string for a completely different and correct reason.

WHY MICROS
==========

`unit_amount_micros`, not `unit_amount`. Every other money column in this
schema is micros (`cost_basis_micros`, `max_cost_micros`, `price_micros`), and
a single table denominated in cents would be a unit trap on every join. The
API converts at the boundary.

WHY NO BACKFILL
===============

Published tiers are immutable — `quota_tiers_publish_immutable()` refuses any
UPDATE to a row with `published_at IS NOT NULL`. That is not an obstacle to
work around, it is the correct answer: a price is a term of sale, and changing
the terms of a tier customers are already on is exactly what versioning exists
to prevent. Existing published tiers therefore keep NULL prices and continue
to render "Contact us for pricing" honestly. Prices arrive by publishing v2,
which `seed_quota_tiers.py` does.

`ALTER TABLE ... ADD COLUMN` does not fire row-level UPDATE triggers, so adding
these columns to a table full of published rows is safe. Attempting an UPDATE
backfill afterwards would raise 42501 on every published row.

THE TRIGGER REPLACEMENT — READ THIS BEFORE ADDING A COLUMN TO THIS TABLE
========================================================================

`quota_tiers_publish_immutable()` enumerated the columns that must not change:

    IF (   NEW.id           IS DISTINCT FROM OLD.id
        OR NEW.key          IS DISTINCT FROM OLD.key
        OR NEW.display_name IS DISTINCT FROM OLD.display_name
        ... ) THEN RAISE

That is a DENYLIST, which means every column added to `quota_tiers` after the
trigger was written is MUTABLE BY DEFAULT on a published tier. Adding the four
price columns under the old trigger would have made a published plan's price
silently editable — the one column on this table where that matters most,
unprotected by the very mechanism that protects `display_name`.

Nothing would have caught it. No test asserts a column it does not know about,
no linter reads plpgsql, and the trigger would keep passing its own tests. It
is the same shape as the scope-vocabulary drift this codebase has hit before:
a constraint that does not automatically cover new members of the set it
guards.

This migration inverts it. The replacement compares whole-row `to_jsonb`
projections minus an explicit ALLOWLIST of the three fields permitted to move:

    to_jsonb(NEW) - 'effective_to' - 'is_active' - 'updated_at'
      IS DISTINCT FROM
    to_jsonb(OLD) - 'effective_to' - 'is_active' - 'updated_at'

A column added in ARCH-30 is now immutable on publish WITHOUT ANYONE
REMEMBERING TO EDIT THIS FUNCTION. The directional guards on `effective_to`
(forward once, never reopened) and `is_active` (one-way to false) are retained
verbatim above the comparison, because those two fields are permitted to move
but not permitted to move arbitrarily.

`verify_arch29_tranche2.py` G1 asserts the function contains no per-column
`IS DISTINCT FROM` enumeration, so a future migration that "fixes" this by
reverting to the readable enumerated form fails the gate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch29_step1_quota_tier_commercials"
down_revision = "arch27_step3_revenue_share_ledger"
branch_labels = None
depends_on = None


# The allowlist. Three fields may change on a published tier:
#   effective_to  — closing the window, forward, once
#   is_active     — deactivation, one-way
#   updated_at    — the TimestampMixin's own bookkeeping
#
# Everything else, including every column added after this migration, is
# immutable once `published_at` is set.
MUTABLE_AFTER_PUBLISH = ("effective_to", "is_active", "updated_at")

_STRIP = " ".join(f"- '{name}'" for name in MUTABLE_AFTER_PUBLISH)

TIER_IMMUTABILITY_FUNCTION_V2 = f"""
CREATE OR REPLACE FUNCTION quota_tiers_publish_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% is published and cannot be deleted; '
                'publish a superseding version instead',
                OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    -- Directional guards. These two fields are in the allowlist below, so the
    -- structural comparison will not catch a bad value for them; only these
    -- clauses will.
    IF (NEW.effective_to IS DISTINCT FROM OLD.effective_to) THEN
        IF OLD.effective_to IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% already closed at %; a closed window is '
                'immutable',
                OLD.key, OLD.version, OLD.effective_to
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to IS NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% cannot be re-opened', OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to <= OLD.effective_from THEN
            RAISE EXCEPTION
                'quota_tiers %/v% effective_to % precedes effective_from %',
                OLD.key, OLD.version, NEW.effective_to, OLD.effective_from
                USING ERRCODE = '22007';
        END IF;
    END IF;

    IF (NEW.is_active IS DISTINCT FROM OLD.is_active) AND NEW.is_active THEN
        RAISE EXCEPTION
            'quota_tiers %/v% cannot be reactivated once deactivated',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    -- ARCH-29. Structural, not enumerated. See the migration docstring: the
    -- previous form was a denylist, so any column added later was mutable by
    -- default on a published tier. This form denies by default and must NOT be
    -- rewritten back into a column-by-column comparison.
    IF (
        (to_jsonb(NEW) {_STRIP})
        IS DISTINCT FROM
        (to_jsonb(OLD) {_STRIP})
    ) THEN
        RAISE EXCEPTION
            'quota_tiers %/v% is published and immutable; only effective_to '
            '(once, forward) and is_active (once, to false) may change',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


TIER_IMMUTABILITY_FUNCTION_V1 = """
CREATE OR REPLACE FUNCTION quota_tiers_publish_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% is published and cannot be deleted; '
                'publish a superseding version instead',
                OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    IF (NEW.effective_to IS DISTINCT FROM OLD.effective_to) THEN
        IF OLD.effective_to IS NOT NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% already closed at %; a closed window is '
                'immutable',
                OLD.key, OLD.version, OLD.effective_to
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to IS NULL THEN
            RAISE EXCEPTION
                'quota_tiers %/v% cannot be re-opened', OLD.key, OLD.version
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to <= OLD.effective_from THEN
            RAISE EXCEPTION
                'quota_tiers %/v% effective_to % precedes effective_from %',
                OLD.key, OLD.version, NEW.effective_to, OLD.effective_from
                USING ERRCODE = '22007';
        END IF;
    END IF;

    IF (NEW.is_active IS DISTINCT FROM OLD.is_active) AND NEW.is_active THEN
        RAISE EXCEPTION
            'quota_tiers %/v% cannot be reactivated once deactivated',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    IF (
        NEW.id                      IS DISTINCT FROM OLD.id
        OR NEW.key                  IS DISTINCT FROM OLD.key
        OR NEW.display_name         IS DISTINCT FROM OLD.display_name
        OR NEW.version              IS DISTINCT FROM OLD.version
        OR NEW.effective_from       IS DISTINCT FROM OLD.effective_from
        OR NEW.published_at         IS DISTINCT FROM OLD.published_at
        OR NEW.published_by_user_id IS DISTINCT FROM OLD.published_by_user_id
        OR NEW.notes                IS DISTINCT FROM OLD.notes
        OR NEW.details              IS DISTINCT FROM OLD.details
        OR NEW.created_at           IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'quota_tiers %/v% is published and immutable; only effective_to '
            '(once, forward) and is_active (once, to false) may change',
            OLD.key, OLD.version
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # ---- commercial columns, all nullable -----------------------------------
    op.add_column(
        "quota_tiers",
        sa.Column("unit_amount_micros", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "quota_tiers",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "quota_tiers",
        sa.Column("billing_interval", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "quota_tiers",
        sa.Column("gateway_price_id", sa.String(length=255), nullable=True),
    )

    # Priced, or not priced. A partially priced tier is not a cheaper tier,
    # it is an unusable one.
    #
    # The zero exemption is not a loophole. A tier at 0 needs a currency and an
    # interval — the card still has to say "Free / month" in some currency —
    # but it has no gateway price because no checkout occurs: a free plan is
    # assigned, not bought. Forcing a `gateway_price_id` onto it would mean
    # creating a $0 price at the gateway purely to satisfy a constraint, and
    # the first person to wonder what that price object is for would be right
    # to.
    op.create_check_constraint(
        "ck_quota_tiers_price_complete",
        "quota_tiers",
        "(unit_amount_micros IS NULL AND currency IS NULL "
        " AND billing_interval IS NULL AND gateway_price_id IS NULL) "
        "OR (unit_amount_micros IS NOT NULL AND currency IS NOT NULL "
        "    AND billing_interval IS NOT NULL "
        "    AND (gateway_price_id IS NOT NULL OR unit_amount_micros = 0))",
    )

    # A free plan is priced at zero, not unpriced. Negative is never a price.
    op.create_check_constraint(
        "ck_quota_tiers_price_non_negative",
        "quota_tiers",
        "unit_amount_micros IS NULL OR unit_amount_micros >= 0",
    )

    op.create_check_constraint(
        "ck_quota_tiers_currency_shape",
        "quota_tiers",
        "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
    )

    # Closed vocabulary. A new interval is a deliberate migration, not a typo
    # that reaches a gateway.
    op.create_check_constraint(
        "ck_quota_tiers_interval_known",
        "quota_tiers",
        "billing_interval IS NULL OR billing_interval IN ('month', 'year')",
    )

    # One gateway price maps to one tier version. Two tiers sharing a price id
    # IS defect F-2 expressed in data, so the database refuses it outright
    # rather than trusting every future call site to derive it per-tier.
    op.create_index(
        "uq_quota_tiers_gateway_price_id",
        "quota_tiers",
        ["gateway_price_id"],
        unique=True,
        postgresql_where=sa.text("gateway_price_id IS NOT NULL"),
    )

    # ---- trigger replacement ------------------------------------------------
    op.execute(TIER_IMMUTABILITY_FUNCTION_V2)


def downgrade() -> None:
    op.execute(TIER_IMMUTABILITY_FUNCTION_V1)

    op.drop_index("uq_quota_tiers_gateway_price_id", table_name="quota_tiers")
    op.drop_constraint(
        "ck_quota_tiers_interval_known", "quota_tiers", type_="check"
    )
    op.drop_constraint(
        "ck_quota_tiers_currency_shape", "quota_tiers", type_="check"
    )
    op.drop_constraint(
        "ck_quota_tiers_price_non_negative", "quota_tiers", type_="check"
    )
    op.drop_constraint(
        "ck_quota_tiers_price_complete", "quota_tiers", type_="check"
    )

    op.drop_column("quota_tiers", "gateway_price_id")
    op.drop_column("quota_tiers", "billing_interval")
    op.drop_column("quota_tiers", "currency")
    op.drop_column("quota_tiers", "unit_amount_micros")