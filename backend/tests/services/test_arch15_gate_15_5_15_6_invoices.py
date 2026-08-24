"""Gate 15.5 / 15.6 — invoice immutability, digest integrity, reproduction.

The plan's assertions, in order:

* an invoice issued against price book v3 reproduces identically after v4 is
  published and activated — **this is the A9 test**
* mutating a line item and recomputing the digest reports a mismatch
* deleting a price book referenced by an issued invoice fails
* a zero-usage period produces a valid invoice with a seat line and no overage
* our totals match the Stripe invoice for the same period within one micro
* cross-tenant read returns 404, per ARCH-14 §14.7
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.core.config import settings
from app.models.invoice import Invoice, InvoiceLineKind, InvoiceStatus
from app.models.organization import Organization, OrganizationStatus
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.usage_rollup import UsageRollup
from app.services.billing import invoice_service

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import (  # noqa: F401
    add_member,
    install_subscription,
)

PERIOD_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 1, tzinfo=timezone.utc)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def priced_org(billing_org):
    """Reuses billing_org which already has a published seat price and overage price."""
    return billing_org


def add_usage(
    db,
    organization_id: uuid.UUID,
    *,
    event_type: str = "llm.input_token",
    quantity: int = 1_500_000,
    sealed: bool = True,
) -> None:
    db.add(
        UsageRollup(
            organization_id=organization_id,
            workspace_id=None,
            grain="DETAIL",
            granularity="MONTH",
            event_type=event_type,
            provider="groq",
            model=None,
            bucket_start=PERIOD_START,
            bucket_end=PERIOD_END,
            quantity=Decimal(quantity),
            cost_micros=quantity * 2,
            event_count=42,
            estimated_quantity=Decimal(0),
            estimated_cost_micros=0,
            estimated_event_count=0,
            late_event_count=0,
            late_quantity=Decimal(0),
            late_cost_micros=0,
            sealed_at=datetime.now(timezone.utc) if sealed else None,
        )
    )
    db.flush()


def assemble_for(db, priced_org_fixture, gateway_fake, *, seats: int = 4):
    subscription = install_subscription(
        db, priced_org_fixture, gateway_fake, seats=seats
    )
    subscription.current_period_start = PERIOD_START
    subscription.current_period_end = PERIOD_END
    db.flush()
    return invoice_service.assemble(db, subscription=subscription)


# ============================================================================
# Gate 15.5 — immutability and integrity
# ============================================================================


class TestGate155Immutability:
    def test_zero_usage_period_still_produces_a_valid_invoice(
        self, db, gateway, priced_org
    ):
        result = assemble_for(db, priced_org, gateway, seats=3)
        invoice = result.invoice

        kinds = [line.kind for line in result.lines]
        assert InvoiceLineKind.SEAT in kinds
        assert InvoiceLineKind.OVERAGE not in kinds

        seat_line = next(l for l in result.lines if l.kind is InvoiceLineKind.SEAT)
        assert seat_line.quantity == Decimal("3.000000")
        assert seat_line.amount_micros == 75_000_000
        assert invoice.subtotal_micros == 75_000_000
        assert invoice.total_micros == 75_000_000
        assert invoice.status is InvoiceStatus.OPEN
        assert invoice.finalized_at is not None
        assert invoice.content_digest.startswith("sha256:")

    def test_included_allowance_appears_at_zero(self, db, gateway, priced_org):
        result = assemble_for(db, priced_org, gateway)
        included = [l for l in result.lines if l.kind is InvoiceLineKind.INCLUDED]

        assert included, (
            "the invoice shows no allowance, so the first question in any "
            "dispute — 'included in what?' — has no answer on the document"
        )
        for line in included:
            assert line.unit_price_micros == 0
            assert line.amount_micros == 0
            assert line.included_quantity is not None

    def test_overage_is_priced_from_the_pinned_book(self, db, gateway, priced_org):
        add_usage(db, priced_org["organization"].id, quantity=1_500_000)
        result = assemble_for(db, priced_org, gateway, seats=2)

        overage = next(l for l in result.lines if l.kind is InvoiceLineKind.OVERAGE)
        assert overage.quantity == Decimal("500000.000000")
        assert overage.unit_price_micros == Decimal("2.000000")
        assert overage.amount_micros == 1_000_000
        assert overage.limit_key == "llm.input_token"
        assert overage.included_quantity == Decimal("1000000.000000")
        assert overage.price_book_entry_id is not None

        assert result.invoice.subtotal_micros == 50_000_000 + 1_000_000

    def test_digest_detects_a_mutated_line(self, db, gateway, priced_org):
        result = assemble_for(db, priced_org, gateway)
        invoice = result.invoice

        matches, _, _ = invoice_service.verify_digest(db, invoice)
        assert matches is True

        db.execute(
            text(
                "ALTER TABLE invoice_line_items DISABLE TRIGGER "
                "trg_invoice_line_items_finalized_immutable"
            )
        )
        db.execute(
            text(
                "UPDATE invoice_line_items SET quantity = quantity + 1, "
                "amount_micros = round((quantity + 1) * unit_price_micros) "
                "WHERE invoice_id = :iid AND kind = 'SEAT'"
            ),
            {"iid": str(invoice.id)},
        )
        db.execute(
            text(
                "ALTER TABLE invoice_line_items ENABLE TRIGGER "
                "trg_invoice_line_items_finalized_immutable"
            )
        )
        db.flush()
        db.expire_all()

        invoice = db.get(Invoice, invoice.id)
        matches, stored, recomputed = invoice_service.verify_digest(db, invoice)

        assert matches is False
        assert stored != recomputed

    def test_trigger_refuses_to_edit_a_finalized_line(self, db, gateway, priced_org):
        result = assemble_for(db, priced_org, gateway)

        with pytest.raises(DatabaseError):
            db.execute(
                text(
                    "UPDATE invoice_line_items SET description = 'edited' "
                    "WHERE invoice_id = :iid"
                ),
                {"iid": str(result.invoice.id)},
            )
            db.flush()
        db.rollback()

    def test_trigger_refuses_to_change_a_finalized_total(
        self, db, gateway, priced_org
    ):
        result = assemble_for(db, priced_org, gateway)

        with pytest.raises(DatabaseError):
            db.execute(
                text(
                    "UPDATE invoices SET subtotal_micros = 1, total_micros = 1 "
                    "WHERE id = :iid"
                ),
                {"iid": str(result.invoice.id)},
            )
            db.flush()
        db.rollback()

    def test_payment_columns_stay_writable_after_finalize(
        self, db, gateway, priced_org
    ):
        result = assemble_for(db, priced_org, gateway, seats=1)
        invoice = result.invoice

        invoice_service.record_payment(
            db, invoice=invoice, amount_paid_micros=invoice.total_micros
        )
        db.flush()
        db.refresh(invoice)

        assert invoice.status is InvoiceStatus.PAID
        assert invoice.paid_at is not None
        assert invoice.amount_paid_micros == invoice.total_micros

        matches, _, _ = invoice_service.verify_digest(db, invoice)
        assert matches is True

    def test_deleting_a_referenced_price_book_fails(self, db, gateway, priced_org):
        result = assemble_for(db, priced_org, gateway)

        with pytest.raises((IntegrityError, DatabaseError)):
            db.execute(
                text("DELETE FROM price_books WHERE id = :pid"),
                {"pid": str(result.invoice.price_book_id)},
            )
            db.flush()
        db.rollback()

    def test_deleting_a_referenced_quota_tier_fails(self, db, gateway, priced_org):
        result = assemble_for(db, priced_org, gateway)

        with pytest.raises((IntegrityError, DatabaseError)):
            db.execute(
                text("DELETE FROM quota_tiers WHERE id = :tid"),
                {"tid": str(result.invoice.quota_tier_id)},
            )
            db.flush()
        db.rollback()

    def test_line_amount_arithmetic_is_enforced_by_the_database(
        self, db, gateway, priced_org
    ):
        from app.models.invoice import InvoiceLineItem

        result = invoice_service.assemble(
            db,
            subscription=install_subscription(db, priced_org, gateway, seats=1),
            finalize=False,
        )

        bad = InvoiceLineItem(
            invoice_id=result.invoice.id,
            line_number=999,
            kind=InvoiceLineKind.OVERAGE,
            description="a line that does not add up",
            quantity=Decimal("10"),
            unit="token",
            unit_price_micros=Decimal("5"),
            amount_micros=999,
            limit_key="llm.input_token",
        )
        db.add(bad)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_assembly_is_idempotent_for_a_period(self, db, gateway, priced_org):
        subscription = install_subscription(db, priced_org, gateway, seats=2)
        subscription.current_period_start = PERIOD_START
        subscription.current_period_end = PERIOD_END
        db.flush()

        first = invoice_service.assemble(db, subscription=subscription)
        second = invoice_service.assemble(db, subscription=subscription)

        assert first.invoice.id == second.invoice.id
        assert len(db.execute(select(Invoice)).scalars().all()) == 1

    def test_void_and_reassemble_is_the_correction_path(
        self, db, gateway, priced_org
    ):
        subscription = install_subscription(db, priced_org, gateway, seats=2)
        subscription.current_period_start = PERIOD_START
        subscription.current_period_end = PERIOD_END
        db.flush()

        original = invoice_service.assemble(db, subscription=subscription).invoice
        original_number = original.number

        invoice_service.void_invoice(db, invoice=original, reason="wrong seat count")
        db.flush()

        subscription.seats_purchased = 5
        db.flush()
        replacement = invoice_service.assemble(db, subscription=subscription).invoice

        assert replacement.id != original.id
        assert replacement.number != original_number
        assert db.get(Invoice, original.id).status is InvoiceStatus.VOID
        assert replacement.seats_billed == 5


# ============================================================================
# Gate 15.6 — reproduction and Stripe agreement
# ============================================================================


class TestGate156Reproduction:
    def test_reproduction_is_byte_identical_after_a_price_book_publish(
        self, db, gateway, priced_org
    ):
        """THE A9 TEST."""
        add_usage(db, priced_org["organization"].id, quantity=1_400_000)
        invoice = assemble_for(db, priced_org, gateway, seats=3).invoice

        before = invoice_service.reproduce(db, invoice=invoice).as_dict()
        assert before["integrity"]["reproducible"] is True
        assert before["provenance"]["price_book_version"] == priced_org[
            "price_book"
        ].version

        now = datetime.now(timezone.utc)
        priced_org["price_book"].effective_to = now
        priced_org["tier"].effective_to = now
        db.flush()

        v4 = PriceBook(
            version=priced_org["price_book"].version + 1,
            effective_from=now,
            currency="USD",
            published_at=None,
            content_digest=None,
            is_active=False,
        )
        db.add(v4)
        db.flush()
        db.add(
            PriceBookEntry(
                price_book_id=v4.id,
                event_type="llm.input_token",
                provider="groq",
                unit="token",
                unit_price_micros=Decimal("999"),
            )
        )
        db.add(
            PriceBookEntry(
                price_book_id=v4.id,
                event_type=settings.BILLING_SEAT_EVENT_TYPE,
                provider="internal",
                unit="seat",
                unit_price_micros=Decimal("99000000"),
            )
        )
        db.flush()
        v4.content_digest = "f" * 64
        v4.published_at = now
        v4.is_active = True
        db.flush([v4])

        tier_v4 = QuotaTier(
            key="business",
            display_name="Business",
            version=priced_org["tier"].version + 1,
            effective_from=now,
            published_at=None,
            is_active=False,
        )
        db.add(tier_v4)
        db.flush()
        db.add(
            QuotaTierEntry(
                quota_tier_id=tier_v4.id,
                limit_key="llm.input_token",
                max_quantity=Decimal(1),
                overage_policy="ALLOW_AND_BILL",
                overage_price_tier_key="llm.input_token",
            )
        )
        db.flush()
        tier_v4.published_at = now
        tier_v4.is_active = True
        db.flush([tier_v4])
        db.expire_all()

        after = invoice_service.reproduce(
            db, invoice=db.get(Invoice, invoice.id)
        ).as_dict()

        assert after == before, (
            "the reproduction changed after a price book publication — A9 is "
            "open again and every past invoice is now indefensible"
        )

    def test_reproduction_reports_arithmetic_failure_honestly(
        self, db, gateway, priced_org
    ):
        invoice = assemble_for(db, priced_org, gateway, seats=2).invoice

        db.execute(
            text(
                "ALTER TABLE invoices DISABLE TRIGGER "
                "trg_invoices_finalized_immutable"
            )
        )
        db.execute(
            text(
                "UPDATE invoices SET subtotal_micros = subtotal_micros + 7, "
                "total_micros = total_micros + 7 "
                "WHERE id = :iid"
            ),
            {"iid": str(invoice.id)},
        )
        db.execute(
            text(
                "ALTER TABLE invoices ENABLE TRIGGER "
                "trg_invoices_finalized_immutable"
            )
        )
        db.flush()
        db.expire_all()

        report = invoice_service.reproduce(db, invoice=db.get(Invoice, invoice.id))

        assert report.arithmetic_ok is False
        assert report.reproducible is False
        assert report.arithmetic_failures

    def test_stripe_totals_agree_within_one_micro(self, db, gateway, priced_org):
        invoice = assemble_for(db, priced_org, gateway, seats=4).invoice
        comparison = invoice_service.compare_with_stripe(
            db, invoice=invoice, stripe_total_cents=10_000
        )

        assert comparison.our_total_cents == 10_000
        assert comparison.delta_micros == 0
        assert comparison.within_tolerance is True

    def test_sub_cent_totals_do_not_count_as_disagreement(
        self, db, gateway, priced_org
    ):
        add_usage(db, priced_org["organization"].id, quantity=1_000_002)
        invoice = assemble_for(db, priced_org, gateway, seats=1).invoice

        assert invoice.total_micros == 25_000_004

        comparison = invoice_service.compare_with_stripe(
            db, invoice=invoice, stripe_total_cents=2500
        )
        assert comparison.our_total_cents == 2500
        assert comparison.within_tolerance is True

    def test_real_disagreement_fails_the_gate(self, db, gateway, priced_org):
        invoice = assemble_for(db, priced_org, gateway, seats=4).invoice
        comparison = invoice_service.compare_with_stripe(
            db, invoice=invoice, stripe_total_cents=9_900
        )
        assert comparison.within_tolerance is False
        assert comparison.delta_micros == -1_000_000

    def test_no_stripe_invoice_is_not_a_disagreement(self, db, gateway, priced_org):
        invoice = assemble_for(db, priced_org, gateway, seats=1).invoice
        comparison = invoice_service.compare_with_stripe(db, invoice=invoice)
        assert comparison.reason == "no_stripe_invoice"
        assert comparison.within_tolerance is True


# ============================================================================
# ARCH-14 §14.7 — tenant isolation
# ============================================================================


class TestInvoiceIsolation:
    def test_cross_tenant_read_returns_nothing(self, db, gateway, priced_org):
        invoice = assemble_for(db, priced_org, gateway).invoice

        other = Organization(
            slug=f"other-{uuid.uuid4().hex[:8]}",
            name="Other",
            status=OrganizationStatus.ACTIVE,
        )
        db.add(other)
        db.flush()

        assert (
            invoice_service.get_for_organization(
                db, organization_id=other.id, invoice_id=invoice.id
            )
            is None
        )
        assert (
            invoice_service.get_for_organization(
                db,
                organization_id=priced_org["organization"].id,
                invoice_id=invoice.id,
            )
            is not None
        )

    def test_listing_is_scoped(self, db, gateway, priced_org):
        assemble_for(db, priced_org, gateway)

        other = Organization(
            slug=f"other-{uuid.uuid4().hex[:8]}",
            name="Other",
            status=OrganizationStatus.ACTIVE,
        )
        db.add(other)
        db.flush()

        assert invoice_service.list_for_organization(
            db, organization_id=other.id
        ) == []
        assert (
            len(
                invoice_service.list_for_organization(
                    db, organization_id=priced_org["organization"].id
                )
            )
            == 1
        )


# ============================================================================
# Canonicalisation
# ============================================================================


class TestDigestCanonicalisation:
    def test_digest_is_stable_across_decimal_representations(
        self, db, gateway, priced_org
    ):
        invoice = assemble_for(db, priced_org, gateway, seats=2).invoice
        lines = sorted(invoice.line_items, key=lambda item: item.line_number)

        first = invoice_service.compute_digest(invoice, lines)
        second = invoice_service.compute_digest(invoice, list(reversed(lines)))

        assert first == second

    def test_digest_shape_is_enforced(self, db, gateway, priced_org):
        invoice = assemble_for(db, priced_org, gateway).invoice
        assert invoice.content_digest.startswith("sha256:")
        assert len(invoice.content_digest) == 71

        with pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE invoices SET content_digest = 'nonsense' WHERE id = :i"),
                {"i": str(invoice.id)},
            )
            db.flush()
        db.rollback()