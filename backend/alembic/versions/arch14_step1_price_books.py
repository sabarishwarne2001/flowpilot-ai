"""ARCH-14 Step 1 — price_books, price_book_entries (EXPAND)

Revision ID: arch14_step1_price_books
Revises: arch12_step7_notification_deliveries
Create Date: 2026-08-20

Closes finding B1. Prices move out of `ai_settings` — a tenant-writable table —
into a platform-owned, versioned, publish-immutable book.

The two triggers are the load-bearing part of this migration. Without them
"published books are immutable" is a convention in a docstring, and the first
time someone fixes a typo in last quarter's price with an UPDATE, every
invoice from that quarter silently changes. With them it is an error at the
database, which is where a financial-evidence guarantee belongs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch14_step1_price_books"
down_revision = "arch12_step7_notification_deliveries"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# price_books: a published book may only be closed forward or deactivated.
# ---------------------------------------------------------------------------
PRICE_BOOK_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION price_books_publish_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'price_books v% is published and cannot be deleted; '
                'publish a superseding version instead',
                OLD.version
                USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;

    -- Drafts are freely editable. That is the whole point of a draft.
    IF OLD.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    -- Published. Exactly two transitions are legal.
    --
    --   1. effective_to: NULL -> a timestamp at or after effective_from.
    --      This is how the publish of version N+1 closes version N's window.
    --      Re-closing an already-closed book would retroactively change which
    --      book prices a past instant, so it is refused.
    --   2. is_active: true -> false. Deactivation stops a book being
    --      resolved for *new* usage; it does not alter what it priced.

    IF (NEW.effective_to IS DISTINCT FROM OLD.effective_to) THEN
        IF OLD.effective_to IS NOT NULL THEN
            RAISE EXCEPTION
                'price_books v% already has effective_to = %; a closed '
                'window is immutable',
                OLD.version, OLD.effective_to
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to IS NULL THEN
            RAISE EXCEPTION
                'price_books v% cannot be re-opened', OLD.version
                USING ERRCODE = '42501';
        END IF;
        IF NEW.effective_to <= OLD.effective_from THEN
            RAISE EXCEPTION
                'price_books v% effective_to % precedes effective_from %',
                OLD.version, NEW.effective_to, OLD.effective_from
                USING ERRCODE = '22007';
        END IF;
    END IF;

    IF (NEW.is_active IS DISTINCT FROM OLD.is_active) AND NEW.is_active THEN
        RAISE EXCEPTION
            'price_books v% cannot be reactivated once deactivated',
            OLD.version
            USING ERRCODE = '42501';
    END IF;

    IF (
        NEW.id                      IS DISTINCT FROM OLD.id
        OR NEW.version              IS DISTINCT FROM OLD.version
        OR NEW.effective_from       IS DISTINCT FROM OLD.effective_from
        OR NEW.currency             IS DISTINCT FROM OLD.currency
        OR NEW.published_at         IS DISTINCT FROM OLD.published_at
        OR NEW.published_by_user_id IS DISTINCT FROM OLD.published_by_user_id
        OR NEW.content_digest       IS DISTINCT FROM OLD.content_digest
        OR NEW.notes                IS DISTINCT FROM OLD.notes
        OR NEW.details              IS DISTINCT FROM OLD.details
        OR NEW.created_at           IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'price_books v% is published and immutable; only effective_to '
            '(once, forward) and is_active (once, to false) may change',
            OLD.version
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


# ---------------------------------------------------------------------------
# price_book_entries: frozen entirely once the parent book is published.
# ---------------------------------------------------------------------------
PRICE_BOOK_ENTRY_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION price_book_entries_publish_immutable()
RETURNS TRIGGER AS $$
DECLARE
    parent_published TIMESTAMPTZ;
    parent_version   INTEGER;
    target_book      UUID;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        target_book := OLD.price_book_id;
    ELSE
        target_book := NEW.price_book_id;
    END IF;

    SELECT pb.published_at, pb.version
      INTO parent_published, parent_version
      FROM price_books pb
     WHERE pb.id = target_book;

    -- Parent already gone: this is the cascade from deleting a draft book.
    IF NOT FOUND THEN
        RETURN COALESCE(OLD, NEW);
    END IF;

    IF parent_published IS NOT NULL THEN
        RAISE EXCEPTION
            'price_book_entries for published book v% are immutable '
            '(attempted %); publish a superseding version instead',
            parent_version, TG_OP
            USING ERRCODE = '42501';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "price_books",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["published_by_user_id"],
            ["users.id"],
            name="fk_price_books_published_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("version > 0", name="ck_price_books_version_positive"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_price_books_effective_window_ordered",
        ),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_price_books_currency_iso4217"
        ),
        sa.CheckConstraint(
            "NOT is_active OR published_at IS NOT NULL",
            name="ck_price_books_active_implies_published",
        ),
        sa.CheckConstraint(
            "(published_at IS NULL) = (content_digest IS NULL)",
            name="ck_price_books_digest_iff_published",
        ),
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_price_books_version ON price_books (version)"
    )
    op.execute(
        "CREATE INDEX ix_price_books_effective "
        "ON price_books (effective_from, effective_to) "
        "WHERE is_active AND published_at IS NOT NULL"
    )

    op.create_table(
        "price_book_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("price_book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("tier_key", sa.String(length=64), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column(
            "unit_price_micros", sa.Numeric(precision=20, scale=9), nullable=False
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
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
            ["price_book_id"],
            ["price_books.id"],
            name="fk_price_book_entries_price_book_id_price_books",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "unit_price_micros >= 0", name="ck_price_book_entries_price_non_negative"
        ),
        sa.CheckConstraint(
            "length(event_type) > 0",
            name="ck_price_book_entries_event_type_not_blank",
        ),
        sa.CheckConstraint(
            "length(provider) > 0", name="ck_price_book_entries_provider_not_blank"
        ),
        sa.CheckConstraint(
            "length(unit) > 0", name="ck_price_book_entries_unit_not_blank"
        ),
    )

    op.create_index(
        "ix_price_book_entries_lookup",
        "price_book_entries",
        ["price_book_id", "event_type", "provider"],
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_price_book_entries_scope "
        "ON price_book_entries ("
        "  price_book_id, event_type, provider, "
        "  COALESCE(model, ''), COALESCE(tier_key, '')"
        ")"
    )

    op.execute(PRICE_BOOK_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_price_books_publish_immutable
        BEFORE UPDATE OR DELETE ON price_books
        FOR EACH ROW EXECUTE FUNCTION price_books_publish_immutable();
        """
    )

    op.execute(PRICE_BOOK_ENTRY_IMMUTABILITY_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_price_book_entries_publish_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON price_book_entries
        FOR EACH ROW EXECUTE FUNCTION price_book_entries_publish_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_price_book_entries_publish_immutable "
        "ON price_book_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS price_book_entries_publish_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_price_books_publish_immutable ON price_books"
    )
    op.execute("DROP FUNCTION IF EXISTS price_books_publish_immutable()")
    op.drop_table("price_book_entries")
    op.drop_table("price_books")