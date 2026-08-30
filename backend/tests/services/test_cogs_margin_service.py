"""Gate 18.1 — cost basis, honest unknowns, and the variance loop.

The load-bearing tests here are the negative ones. Anyone can assert that
revenue minus cost equals margin; the tests that matter are the ones asserting
the system refuses to guess:

    T4  a row with no cost basis contributes NOTHING to a margin, and is
        counted in unknown_cost_share rather than treated as free
    T5  an undeclared zero is refused at the database
    T6  cost_basis_micros cannot be UPDATEd after the fact  <-- the invariant
    T11 an invoice against a period we modelled nothing in produces a NULL
        ratio and INVESTIGATE, not a tidy 0.0 match

T6 is the one to keep if you keep only one. It is the whole reason the
migration rewrites `usage_events_immutable()`, and without it a future ALTER
TABLE could silently reopen the hole.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, InternalError
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.models.price_book import PriceBook
from app.models.supplier_cogs import (
    STATUS_INVESTIGATE,
    STATUS_MATCHED,
    SupplierInvoice,
)
from app.models.usage_event import UsageEvent
from app.services import (
    cost_basis_service,
    margin_service,
    pricing_service,
    usage_service,
)
from app.services import supplier_reconciliation_service as recon
from app.services.cost_basis_service import InvalidCostBasisError
from app.services.pricing_service import PriceBookValidationError, PriceSpec

pytestmark = pytest.mark.usefixtures("test_database")

# Charge 1.0 micro per token, pay 0.4. A 60% gross margin, chosen so the
# expected numbers are checkable by eye in a failure message.
PRICE_MICROS = Decimal("1.000000000")
COST_MICROS = Decimal("0.400000000")


@pytest.fixture(autouse=True)
def _no_price_cache(monkeypatch):
    monkeypatch.setattr(
        pricing_service.settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    pricing_service.clear_cache()
    yield
    pricing_service.clear_cache()


@pytest.fixture()
def org(db_session: Session) -> Organization:
    record = Organization(
        slug=f"cogs-{uuid.uuid4().hex[:8]}",
        name="COGS Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(record)
    db_session.flush()
    return record


@pytest.fixture()
def other_org(db_session: Session) -> Organization:
    record = Organization(
        slug=f"cogs2-{uuid.uuid4().hex[:8]}",
        name="Thin Margin Ltd.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _publish(db: Session, *, version: int, with_cost: bool) -> PriceBook:
    effective = datetime.now(timezone.utc) - timedelta(days=90)
    specs = [
        PriceSpec(
            event_type="llm.input_token",
            provider="groq",
            unit_price_micros=PRICE_MICROS,
            cost_basis_micros=COST_MICROS if with_cost else None,
            cost_basis_source="SUPPLIER_RATE_CARD" if with_cost else None,
        ),
        PriceSpec(
            event_type="llm.output_token",
            provider="groq",
            unit_price_micros=PRICE_MICROS,
            cost_basis_micros=COST_MICROS if with_cost else None,
            cost_basis_source="SUPPLIER_RATE_CARD" if with_cost else None,
        ),
    ]
    return pricing_service.publish(
        db, version=version, effective_from=effective, entries=specs
    )


def _record(
    db: Session,
    org_id: uuid.UUID,
    *,
    quantity: int,
    cost_basis: int | None,
    source: str | None,
    occurred_at: datetime | None = None,
    provider: str = "groq",
    price_book_id: uuid.UUID | None = None,
) -> UsageEvent:
    unit_price = PRICE_MICROS if price_book_id else None
    return usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="llm.input_token",
        quantity=Decimal(quantity),
        cost_micros=int(Decimal(quantity) * PRICE_MICROS) if price_book_id else None,
        price_book_id=price_book_id,
        unit_price_micros=unit_price,
        cost_basis_micros=cost_basis,
        cost_basis_source=source,
        provider=provider,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        idempotency_key=f"t-{uuid.uuid4().hex}",
        require_active_transaction=False,
    )


# =========================================================================
# T1-T3 — rate card resolution
# =========================================================================


def test_t1_cost_basis_resolves_from_the_same_entry_as_the_price(db_session: Session):
    """Both sides of the margin must come from one book version, one instant."""
    _publish(db_session, version=1, with_cost=True)

    price = pricing_service.resolve(
        db_session, event_type="llm.input_token", provider="groq"
    )
    basis = cost_basis_service.from_resolved_price(price)

    assert basis.known is True
    assert basis.unit_cost_micros == COST_MICROS
    assert basis.source == "SUPPLIER_RATE_CARD"
    assert basis.price_book_id == price.price_book_id
    assert basis.price_book_version == price.price_book_version
    assert basis.is_hard is True

    # 1000 tokens at 1.0 charged / 0.4 cost.
    assert price.cost_micros(1000) == 1000
    assert price.cost_basis_for(1000) == 400


def test_t2_a_book_without_cost_basis_resolves_to_unknown_not_zero(
    db_session: Session,
):
    """The pre-ARCH-18 state. Reporting must say 'unknown', never 'free'."""
    _publish(db_session, version=1, with_cost=False)

    basis = cost_basis_service.resolve(
        db_session, event_type="llm.input_token", provider="groq"
    )

    assert basis.known is False
    assert basis.unit_cost_micros is None
    assert basis.source is None
    assert basis.cost_micros(1000) is None, (
        "cost_micros() must return None for an unknown basis. Returning 0 here "
        "is the single change that would make every margin in the system wrong "
        "in the flattering direction."
    )
    assert basis.reason == "price_book_entry_has_no_cost_basis"


def test_t3_cost_resolution_never_raises_when_pricing_does(db_session: Session):
    """The refusal asymmetry: an unpriced unit refuses, an uncosted one does not.

    Refusing generation because nobody filled in a supplier rate would take
    the platform down to protect a dashboard.
    """
    with pytest.raises(pricing_service.PriceUnavailableError):
        pricing_service.resolve(
            db_session, event_type="llm.input_token", provider="groq"
        )

    basis = cost_basis_service.resolve(
        db_session, event_type="llm.input_token", provider="groq"
    )
    assert basis.known is False
    assert basis.reason == "no_price_book_covers_instant"


# =========================================================================
# T4-T6 — honest unknowns and immutability
# =========================================================================


def test_t4_unknown_cost_rows_are_excluded_from_margin_and_reported(
    db_session: Session, org: Organization
):
    """The invariant, as arithmetic.

    Two rows of equal revenue: one costed, one not. The margin must be
    computed over the costed row alone, and the uncosted row must show up as
    half the revenue being unaccounted for — not as free money.
    """
    book = _publish(db_session, version=1, with_cost=True)

    _record(db_session, org.id, quantity=1000, cost_basis=400,
            source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    _record(db_session, org.id, quantity=1000, cost_basis=None,
            source=None, price_book_id=book.id)
    db_session.flush()

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    summary = margin_service.platform_summary(
        db_session, period_start=start, period_end=end
    )
    f = summary.figures

    assert f.revenue_micros == 2000
    assert f.attributed_revenue_micros == 1000, (
        "Only revenue on rows with a known cost may enter the margin."
    )
    assert f.cost_basis_micros == 400
    assert f.gross_margin_micros == 600
    assert f.gross_margin_ratio == pytest.approx(0.6)

    assert f.unknown_cost_event_count == 1
    assert f.unknown_cost_revenue_micros == 1000
    assert f.unknown_cost_share == pytest.approx(0.5)

    # Half the revenue unaccounted for is below the trust floor, so the API
    # is required to suppress the headline rather than quote 60%.
    assert f.is_trustworthy is False


def test_t5_an_undeclared_zero_cost_is_refused(db_session: Session, org: Organization):
    """0 with any source but ZERO_BYOK reads as 100% margin. Refused twice.

    Once in Python for a readable message, once by the CHECK constraint so a
    path that bypasses the service still cannot write it.
    """
    with pytest.raises(InvalidCostBasisError, match="ZERO_BYOK"):
        cost_basis_service.validate_cost_basis(0, "SUPPLIER_RATE_CARD")

    with pytest.raises(InvalidCostBasisError):
        cost_basis_service.validate_cost_basis(500, "ZERO_BYOK")

    # And at the database, bypassing the service entirely.
    book = _publish(db_session, version=1, with_cost=True)
    event = _record(db_session, org.id, quantity=10, cost_basis=4,
                    source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    db_session.flush()

    with pytest.raises((IntegrityError, DBAPIError)):
        db_session.execute(
            text(
                "INSERT INTO usage_events "
                "(id, organization_id, event_type, unit, quantity, "
                " cost_basis_micros, cost_basis_source, occurred_at, "
                " created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org, 'llm.input_token', 'token', "
                "        1, 0, 'SUPPLIER_RATE_CARD', now(), now(), now())"
            ),
            {"org": str(org.id)},
        )
    db_session.rollback()
    assert event is not None


def test_t6_cost_basis_cannot_be_rewritten_after_the_fact(
    db_session: Session, org: Organization
):
    """INVARIANT 1. A later rate card edit must not restate a closed quarter.

    This is the test the migration's IMMUTABILITY_FUNCTION_V3 exists for.
    Without the rewrite, `usage_events_immutable()` enumerates only the
    columns that existed before ARCH-18, and an UPDATE to cost_basis_micros
    sails straight through.
    """
    book = _publish(db_session, version=1, with_cost=True)
    event = _record(db_session, org.id, quantity=1000, cost_basis=400,
                    source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    db_session.flush()
    event_id = event.id
    db_session.commit()

    with pytest.raises((InternalError, DBAPIError)) as excinfo:
        db_session.execute(
            text(
                "UPDATE usage_events SET cost_basis_micros = 1 WHERE id = :id"
            ),
            {"id": str(event_id)},
        )
        db_session.flush()
    assert "immutable" in str(excinfo.value).lower()
    db_session.rollback()

    with pytest.raises((InternalError, DBAPIError)):
        db_session.execute(
            text(
                "UPDATE usage_events SET cost_basis_source = 'ESTIMATED' "
                "WHERE id = :id"
            ),
            {"id": str(event_id)},
        )
        db_session.flush()
    db_session.rollback()

    # aggregated_at remains the one permitted UPDATE — ARCH-14's rollup
    # depends on it and this migration must not have broken it.
    db_session.execute(
        text("UPDATE usage_events SET aggregated_at = now() WHERE id = :id"),
        {"id": str(event_id)},
    )
    db_session.commit()

    row = db_session.execute(
        select(UsageEvent).where(UsageEvent.id == event_id)
    ).scalar_one()
    assert row.cost_basis_micros == 400
    assert row.aggregated_at is not None


def test_t7_publishing_a_new_book_does_not_restate_old_rows(
    db_session: Session, org: Organization
):
    """The denormalisation, proven end to end.

    Settle a row against v1, publish v2 at double the cost, assert the
    settled row still reports v1's cost. This is the difference between a
    margin history and a margin opinion.
    """
    book_v1 = _publish(db_session, version=1, with_cost=True)
    event = _record(db_session, org.id, quantity=1000, cost_basis=400,
                    source="SUPPLIER_RATE_CARD", price_book_id=book_v1.id)
    db_session.flush()

    pricing_service.clear_cache()
    pricing_service.publish(
        db_session,
        version=2,
        effective_from=datetime.now(timezone.utc) + timedelta(seconds=1),
        entries=[
            PriceSpec(
                event_type="llm.input_token",
                provider="groq",
                unit_price_micros=PRICE_MICROS,
                cost_basis_micros=Decimal("0.800000000"),
                cost_basis_source="SUPPLIER_RATE_CARD",
            ),
            PriceSpec(
                event_type="llm.output_token",
                provider="groq",
                unit_price_micros=PRICE_MICROS,
                cost_basis_micros=Decimal("0.800000000"),
                cost_basis_source="SUPPLIER_RATE_CARD",
            ),
        ],
    )
    db_session.flush()

    refreshed = db_session.execute(
        select(UsageEvent).where(UsageEvent.id == event.id)
    ).scalar_one()
    assert refreshed.cost_basis_micros == 400


def test_t8_publish_rejects_a_malformed_cost_basis(db_session: Session):
    """A bad rate card must fail at publish, not at the first margin report."""
    with pytest.raises(PriceBookValidationError, match="ZERO_BYOK"):
        pricing_service.publish(
            db_session,
            version=1,
            effective_from=datetime.now(timezone.utc),
            entries=[
                PriceSpec(
                    event_type="llm.input_token",
                    provider="groq",
                    unit_price_micros=PRICE_MICROS,
                    cost_basis_micros=Decimal("0"),
                    cost_basis_source="MEASURED",
                )
            ],
        )

    with pytest.raises(PriceBookValidationError):
        pricing_service.publish(
            db_session,
            version=1,
            effective_from=datetime.now(timezone.utc),
            entries=[
                PriceSpec(
                    event_type="llm.input_token",
                    provider="groq",
                    unit_price_micros=PRICE_MICROS,
                    cost_basis_micros=Decimal("0.4"),
                    cost_basis_source="NOT_A_SOURCE",
                )
            ],
        )


# =========================================================================
# T9-T10 — tenant economics
# =========================================================================


def test_t9_tenant_ranking_puts_the_worst_margin_first(
    db_session: Session, org: Organization, other_org: Organization
):
    book = _publish(db_session, version=1, with_cost=True)

    # org: 1000 revenue, 400 cost -> 60% margin
    _record(db_session, org.id, quantity=1000, cost_basis=400,
            source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    # other_org: 1000 revenue, 950 cost -> 5% margin
    _record(db_session, other_org.id, quantity=1000, cost_basis=950,
            source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    db_session.flush()

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc) + timedelta(hours=1)

    ranked = margin_service.tenant_economics(
        db_session, period_start=start, period_end=end, order="MARGIN_ASC"
    )
    assert [e.organization_id for e in ranked][:2] == [other_org.id, org.id]
    assert ranked[0].figures.gross_margin_ratio == pytest.approx(0.05)
    assert ranked[0].organization_name == "Thin Margin Ltd."

    reversed_order = margin_service.tenant_economics(
        db_session, period_start=start, period_end=end, order="MARGIN_DESC"
    )
    assert reversed_order[0].organization_id == org.id


def test_t10_a_tenant_with_no_known_cost_does_not_top_the_ranking(
    db_session: Session, org: Organization, other_org: Organization
):
    """The failure this whole design exists to prevent.

    A tenant we know nothing about must not appear as the most profitable one
    on the board. With COALESCE(cost, 0) it would rank first at 100%.
    """
    book = _publish(db_session, version=1, with_cost=True)

    _record(db_session, org.id, quantity=1000, cost_basis=400,
            source="SUPPLIER_RATE_CARD", price_book_id=book.id)
    _record(db_session, other_org.id, quantity=5000, cost_basis=None,
            source=None, price_book_id=book.id)
    db_session.flush()

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc) + timedelta(hours=1)

    ranked = margin_service.tenant_economics(
        db_session, period_start=start, period_end=end, order="MARGIN_DESC"
    )
    assert ranked[0].organization_id == org.id, (
        "The uncosted tenant must not outrank a real 60% margin."
    )

    unknown = next(e for e in ranked if e.organization_id == other_org.id)
    assert unknown.figures.gross_margin_ratio is None
    assert unknown.figures.gross_margin_micros is None
    assert unknown.figures.unknown_cost_share == pytest.approx(1.0)
    assert unknown.figures.is_trustworthy is False


# =========================================================================
# T11-T14 — supplier reconciliation
# =========================================================================


def _closed_period() -> tuple[date, date]:
    """A period far enough in the past to be considered final."""
    end = (datetime.now(timezone.utc) - timedelta(days=10)).date()
    return end - timedelta(days=30), end


def test_t11_variance_against_a_zero_modelled_total_is_undefined(
    db_session: Session,
):
    """NULL ratio and INVESTIGATE, never a tidy 0.0 'match'.

    An invoice for a period we modelled nothing in is the most informative
    signal the loop produces — an entire provider is invisible to the cost
    model — and a 0.0 would bury it as a perfect match.
    """
    period_start, period_end = _closed_period()
    invoice = recon.ingest_invoice(
        db_session,
        provider="openai",
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=1_000_000,
    )
    db_session.flush()

    row = recon.reconcile(db_session, supplier_invoice_id=invoice.id)
    db_session.flush()

    assert row.modelled_total_micros == 0
    assert row.variance_micros == 1_000_000
    assert row.variance_ratio is None
    assert row.status == STATUS_INVESTIGATE


def test_t12_variance_math_and_thresholds(db_session: Session, org: Organization):
    book = _publish(db_session, version=1, with_cost=True)
    period_start, period_end = _closed_period()
    inside = datetime.combine(
        period_start + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    # 1_000_000 micros of modelled supplier cost inside the window.
    _record(db_session, org.id, quantity=2_500_000, cost_basis=1_000_000,
            source="SUPPLIER_RATE_CARD", occurred_at=inside,
            price_book_id=book.id)
    db_session.flush()

    # Invoiced 1_010_000 -> +1% variance.
    invoice = recon.ingest_invoice(
        db_session,
        provider="groq",
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=1_010_000,
    )
    db_session.flush()

    tight = recon.compute_variance(
        db_session, invoice=invoice, threshold_ratio=0.005
    )
    assert tight.modelled_total_micros == 1_000_000
    assert tight.variance_micros == 10_000
    assert float(tight.variance_ratio) == pytest.approx(0.01)
    assert tight.status == STATUS_INVESTIGATE

    loose = recon.compute_variance(
        db_session, invoice=invoice, threshold_ratio=0.02
    )
    assert loose.status == STATUS_MATCHED


def test_t13_the_period_end_date_is_inclusive(db_session: Session, org: Organization):
    """Usage on the final day of the invoice period must be counted.

    An off-by-one here drops a day of COGS every month, and nothing in the
    stored row would ever reveal it.
    """
    book = _publish(db_session, version=1, with_cost=True)
    period_start, period_end = _closed_period()

    last_day_late = datetime.combine(
        period_end, datetime.max.time(), tzinfo=timezone.utc
    ).replace(microsecond=0)
    day_after = datetime.combine(
        period_end + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(minutes=1)

    _record(db_session, org.id, quantity=1000, cost_basis=500,
            source="SUPPLIER_RATE_CARD", occurred_at=last_day_late,
            price_book_id=book.id)
    _record(db_session, org.id, quantity=1000, cost_basis=700,
            source="SUPPLIER_RATE_CARD", occurred_at=day_after,
            price_book_id=book.id)
    db_session.flush()

    lower, upper = recon.period_bounds(period_start, period_end)
    assert upper.date() == period_end + timedelta(days=1)
    assert upper.hour == 0

    invoice = recon.ingest_invoice(
        db_session,
        provider="groq",
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=500,
    )
    db_session.flush()

    result = recon.compute_variance(db_session, invoice=invoice)
    assert result.modelled_total_micros == 500, (
        "23:59 on the last day is inside the period; 00:01 the next day is not."
    )


def test_t14_an_open_period_is_refused_and_acceptance_appends(
    db_session: Session, org: Organization
):
    today = datetime.now(timezone.utc).date()
    invoice = recon.ingest_invoice(
        db_session,
        provider="anthropic",
        period_start=today - timedelta(days=3),
        period_end=today,
        invoiced_total_micros=5_000,
    )
    db_session.flush()

    with pytest.raises(recon.PeriodNotClosedError):
        recon.reconcile(db_session, supplier_invoice_id=invoice.id)

    forced = recon.reconcile(
        db_session, supplier_invoice_id=invoice.id, force=True
    )
    db_session.flush()
    assert forced.details["forced"] is True
    assert forced.status == STATUS_INVESTIGATE

    # Acceptance appends; the original finding survives.
    with pytest.raises(recon.SupplierReconciliationError, match="note is required"):
        recon.accept(db_session, reconciliation_id=forced.id, note="   ")

    accepted = recon.accept(
        db_session,
        reconciliation_id=forced.id,
        note="Anthropic minimum commit, not usage-linked.",
    )
    db_session.flush()

    assert accepted.id != forced.id
    assert accepted.details["accepts_reconciliation_id"] == str(forced.id)
    assert accepted.variance_micros == forced.variance_micros

    history = recon.list_reconciliations(
        db_session, supplier_invoice_id=invoice.id
    )
    assert len(history) == 2
    assert history[0].status == "ACCEPTED"
    assert history[1].status == STATUS_INVESTIGATE


def test_t15_a_duplicate_invoice_period_is_refused(db_session: Session):
    period_start, period_end = _closed_period()
    recon.ingest_invoice(
        db_session,
        provider="openai",
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=100,
    )
    db_session.flush()

    with pytest.raises(recon.SupplierInvoiceExistsError):
        recon.ingest_invoice(
            db_session,
            provider="openai",
            period_start=period_start,
            period_end=period_end,
            invoiced_total_micros=200,
        )

    remaining = db_session.execute(
        select(SupplierInvoice).where(SupplierInvoice.provider == "openai")
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].invoiced_total_micros == 100