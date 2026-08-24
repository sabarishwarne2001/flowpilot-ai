"""ARCH-15 Step 15.3 — billing_accounts, subscriptions, billable_seats (EXPAND)

Revision ID: arch15_step3_billing_accounts_and_subscriptions
Revises: arch15_step1_stripe_inbound_events
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch15_step3_billing_accounts_and_subscriptions"
down_revision = "arch15_step1_stripe_inbound_events"
branch_labels = None
depends_on = None


SUBSCRIPTION_STATUS_ENUM_NAME = "subscription_status"

#: Stripe's vocabulary, mirrored rather than translated. The Python enum in
#: `app/models/subscription.py` carries the identical list and Gate 15.3
#: asserts the two agree.
SUBSCRIPTION_STATUS_VALUES: tuple[str, ...] = (
    "incomplete",
    "incomplete_expired",
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "paused",
)

LIVE_STATUS_VALUES: tuple[str, ...] = (
    "trialing",
    "active",
    "past_due",
    "unpaid",
)


def _live_predicate() -> str:
    inner = ", ".join(
        f"'{value}'::{SUBSCRIPTION_STATUS_ENUM_NAME}" for value in LIVE_STATUS_VALUES
    )
    return f"status IN ({inner})"


# F7 — the single-currency assertion.
#
# ARCH-15 does not add multi-currency. It adds a refusal. `price_books` already
# carries a currency; `usage_events.cost_micros` and `spend_limits
# .max_cost_micros` are bare integers with no currency at all. The failure this
# prevents is an invoice that sums micros priced in two currencies, which is
# invisible precisely because both are integers.
#
# Written as a trigger rather than a CHECK because the assertion is
# cross-table, and a CHECK cannot reference another table.
CURRENCY_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION billing_accounts_currency_matches_book()
RETURNS TRIGGER AS $$
DECLARE
    book_currency  TEXT;
    book_version   INTEGER;
BEGIN
    SELECT pb.currency, pb.version
      INTO book_currency, book_version
      FROM price_books pb
     WHERE pb.is_active
       AND pb.published_at IS NOT NULL
       AND pb.effective_from <= now()
       AND (pb.effective_to IS NULL OR pb.effective_to > now())
     ORDER BY pb.version DESC
     LIMIT 1;

    -- No book in force is ARCH-14 Gate 14.1's problem, not this trigger's.
    -- Refusing here would make a tenant uncreatable during the window
    -- between a schema deploy and the first price book publication.
    IF book_currency IS NULL THEN
        RETURN NEW;
    END IF;

    IF upper(NEW.currency) <> upper(book_currency) THEN
        RAISE EXCEPTION
            'billing_accounts.currency % differs from price book v% currency %; '
            'ARCH-15 F7 refuses to mix currencies rather than sum them silently',
            NEW.currency, book_version, book_currency
            USING ERRCODE = '22023';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


BILLABLE_SEATS_VIEW = """
CREATE OR REPLACE VIEW billable_seats AS
SELECT
    om.organization_id                        AS organization_id,
    count(*)::integer                         AS seats,
    max(om.updated_at)                        AS last_membership_change_at
FROM organization_members om
WHERE om.status = 'ACTIVE'::membership_status
GROUP BY om.organization_id
"""


def upgrade() -> None:
    postgresql.ENUM(
        *SUBSCRIPTION_STATUS_VALUES, name=SUBSCRIPTION_STATUS_ENUM_NAME
    ).create(op.get_bind(), checkfirst=True)

    # ---- billing_accounts (F5) ------------------------------------------
    op.create_table(
        "billing_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("billing_email", sa.String(length=320), nullable=False),
        sa.Column("tax_id", sa.String(length=64), nullable=True),
        sa.Column("delinquent_since", sa.DateTime(timezone=True), nullable=True),
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
        # RESTRICT, not CASCADE. Deleting an organization that still has a
        # Stripe customer must fail loudly; the alternative is an orphaned
        # subscription charging a card for a tenant that no longer exists.
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_billing_accounts_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_billing_accounts_currency_iso4217"
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name="ck_billing_accounts_currency_upper"
        ),
        sa.CheckConstraint(
            "length(stripe_customer_id) > 0",
            name="ck_billing_accounts_customer_id_not_blank",
        ),
        sa.CheckConstraint(
            "billing_email = lower(billing_email)",
            name="ck_billing_accounts_billing_email_lowercase",
        ),
        sa.CheckConstraint(
            "position('@' in billing_email) > 1",
            name="ck_billing_accounts_billing_email_shaped",
        ),
    )

    op.create_index(
        "uq_billing_accounts_organization_id",
        "billing_accounts",
        ["organization_id"],
        unique=True,
    )
    op.create_index(
        "uq_billing_accounts_stripe_customer_id",
        "billing_accounts",
        ["stripe_customer_id"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_billing_accounts_delinquent "
        "ON billing_accounts (delinquent_since DESC) "
        "WHERE delinquent_since IS NOT NULL"
    )

    op.execute(CURRENCY_GUARD_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_billing_accounts_currency_matches_book
        BEFORE INSERT OR UPDATE OF currency ON billing_accounts
        FOR EACH ROW EXECUTE FUNCTION billing_accounts_currency_matches_book();
        """
    )

    # ---- subscriptions (F2, F3) -----------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *SUBSCRIPTION_STATUS_VALUES,
                name=SUBSCRIPTION_STATUS_ENUM_NAME,
                create_type=False,
            ),
            nullable=False,
        ),
        # F3 — pinned, not looked up at read time.
        sa.Column("quota_tier_key", sa.String(length=32), nullable=False),
        sa.Column("quota_tier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "seats_purchased",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "current_period_start", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        # F2 — the monotonic guard against a stale fetch landing last.
        sa.Column(
            "stripe_state_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_reconciled_at",
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
            ["billing_account_id"],
            ["billing_accounts.id"],
            name="fk_subscriptions_billing_account_id_billing_accounts",
            ondelete="RESTRICT",
        ),
        # RESTRICT on both pins: a tier version or price book referenced by a
        # subscription — and therefore by every invoice that subscription
        # produced — cannot be deleted. This is half of what makes A9
        # closable in 15.5.
        sa.ForeignKeyConstraint(
            ["quota_tier_id"],
            ["quota_tiers.id"],
            name="fk_subscriptions_quota_tier_id_quota_tiers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["price_book_id"],
            ["price_books.id"],
            name="fk_subscriptions_price_book_id_price_books",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_subscriptions_period_ordered",
        ),
        sa.CheckConstraint(
            "seats_purchased >= 0", name="ck_subscriptions_seats_positive"
        ),
        sa.CheckConstraint(
            "stripe_state_version >= 0",
            name="ck_subscriptions_state_version_non_negative",
        ),
        sa.CheckConstraint(
            "length(stripe_subscription_id) > 0",
            name="ck_subscriptions_stripe_id_not_blank",
        ),
        sa.CheckConstraint(
            "length(quota_tier_key) > 0",
            name="ck_subscriptions_tier_key_not_blank",
        ),
        # NOT the biconditional the plan sketched. Stripe stamps `canceled_at`
        # at the moment a `cancel_at_period_end` cancellation is *requested*,
        # while `status` stays `active` until the period ends. Asserting
        # equivalence would dead-letter the reconcile of every scheduled
        # cancellation. Only the implication is invariant.
        sa.CheckConstraint(
            f"status <> 'canceled'::{SUBSCRIPTION_STATUS_ENUM_NAME} "
            "OR canceled_at IS NOT NULL",
            name="ck_subscriptions_canceled_implies_canceled_at",
        ),
        sa.CheckConstraint(
            "cancel_at_period_end IS FALSE OR canceled_at IS NOT NULL "
            "OR cancel_at IS NOT NULL",
            name="ck_subscriptions_scheduled_cancel_has_a_date",
        ),
    )

    op.create_index(
        "uq_subscriptions_stripe_subscription_id",
        "subscriptions",
        ["stripe_subscription_id"],
        unique=True,
    )
    # One live subscription per account. Partial, so historical canceled rows
    # stay — 15.6 reproduces invoices against them years later.
    op.execute(
        "CREATE UNIQUE INDEX uq_subscriptions_live_account "
        "ON subscriptions (billing_account_id) "
        f"WHERE {_live_predicate()}"
    )
    op.execute(
        "CREATE INDEX ix_subscriptions_account_created "
        "ON subscriptions (billing_account_id, created_at DESC)"
    )
    op.create_index(
        "ix_subscriptions_quota_tier_id", "subscriptions", ["quota_tier_id"]
    )
    op.create_index(
        "ix_subscriptions_price_book_id", "subscriptions", ["price_book_id"]
    )
    op.execute(
        "CREATE INDEX ix_subscriptions_period_end "
        "ON subscriptions (current_period_end) "
        f"WHERE {_live_predicate()}"
    )
    op.create_index(
        "ix_subscriptions_stale_reconcile", "subscriptions", ["last_reconciled_at"]
    )

    # ---- billable_seats (15.4) ------------------------------------------
    # Derived, then asserted. A stored seat count is a count somebody forgets
    # to maintain, and that failure is silent and monetary.
    op.execute(BILLABLE_SEATS_VIEW)

    # `organization_members.status` is the seat predicate, and the seat query
    # runs on every drift check. Partial index so it covers exactly the rows
    # the view reads.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_organization_members_active_seats "
        "ON organization_members (organization_id) "
        "WHERE status = 'ACTIVE'::membership_status"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_organization_members_active_seats")
    op.execute("DROP VIEW IF EXISTS billable_seats")

    op.execute("DROP INDEX IF EXISTS ix_subscriptions_stale_reconcile")
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_period_end")
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_price_book_id")
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_quota_tier_id")
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_account_created")
    op.execute("DROP INDEX IF EXISTS uq_subscriptions_live_account")
    op.execute("DROP INDEX IF EXISTS uq_subscriptions_stripe_subscription_id")
    op.drop_table("subscriptions")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_billing_accounts_currency_matches_book "
        "ON billing_accounts"
    )
    op.execute("DROP FUNCTION IF EXISTS billing_accounts_currency_matches_book()")

    op.execute("DROP INDEX IF EXISTS ix_billing_accounts_delinquent")
    op.execute("DROP INDEX IF EXISTS uq_billing_accounts_stripe_customer_id")
    op.execute("DROP INDEX IF EXISTS uq_billing_accounts_organization_id")
    op.drop_table("billing_accounts")

    postgresql.ENUM(name=SUBSCRIPTION_STATUS_ENUM_NAME).drop(
        op.get_bind(), checkfirst=True
    )