"""Gate 14.8 — invoice reproducibility (A9) and the B1 CONTRACT."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.services import invoice_preview_service, pricing_service, rollup_service
from app.services.invoice_preview_service import (
    NOT_REPRODUCIBLE_LEGACY,
    REPRODUCTION_MONTHS,
)
from app.services.pricing_service import PriceSpec
from tests.services.test_arch14_gate_14_2_rollups import emit

pytestmark = pytest.mark.usefixtures("test_database")

MODEL = "llama-3.3-70b"
V1_MICROS = Decimal("0.200000000")
V2_MICROS = Decimal("0.350000000")


@pytest.fixture(autouse=True)
def _no_price_cache(monkeypatch):
    monkeypatch.setattr(
        pricing_service.settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    pricing_service.clear_cache()
    yield
    pricing_service.clear_cache()


@pytest.fixture()
def org(db_session: Session) -> uuid.UUID:
    organization = Organization(
        slug=f"invoice-{uuid.uuid4().hex[:8]}",
        name="Invoice Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(organization)
    db_session.flush()
    return organization.id


def _entries(micros: Decimal) -> list[PriceSpec]:
    return [
        PriceSpec(
            event_type="llm.input_token",
            provider="groq",
            model=MODEL,
            unit_price_micros=micros,
        ),
        PriceSpec(
            event_type="llm.output_token",
            provider="groq",
            model=MODEL,
            unit_price_micros=micros,
        ),
    ]


def priced_emit(
    db: Session,
    *,
    org: uuid.UUID,
    at: datetime,
    quantity: int,
    unit_price: Decimal,
    book_id: uuid.UUID,
    event_type: str = "llm.input_token",
) -> None:
    cost = pricing_service.cost_micros(quantity, unit_price)
    db.execute(
        text(
            """
            INSERT INTO usage_events (
                organization_id, event_type, unit, quantity, cost_micros,
                price_book_id, unit_price_micros, provider, details, occurred_at
            ) VALUES (
                :org, :event_type, 'token', :quantity, :cost,
                :book, :unit_price, 'groq',
                jsonb_build_object('model', :model, 'price_source', 'price_book'),
                :at
            )
            """
        ),
        {
            "org": org,
            "event_type": event_type,
            "quantity": Decimal(quantity),
            "cost": cost,
            "book": book_id,
            "unit_price": unit_price,
            "model": MODEL,
            "at": at,
        },
    )


def fold_and_seal(db: Session, *, now: datetime) -> None:
    rollup_service.run_rollup(db, now=now)
    rollup_service.seal_due(db, now=now, grace_hours=26)
    db.flush()


@pytest.fixture()
def two_books(db_session: Session):
    month_start = rollup_service.month_bucket(
        datetime.now(timezone.utc) - timedelta(days=60)
    )
    changeover = month_start + timedelta(days=15)

    first = pricing_service.publish(
        db_session,
        version=1,
        effective_from=month_start - timedelta(days=30),
        entries=_entries(V1_MICROS),
    )
    second = pricing_service.publish(
        db_session,
        version=2,
        effective_from=changeover,
        entries=_entries(V2_MICROS),
    )
    db_session.flush()
    pricing_service.clear_cache()
    return month_start, changeover, first, second


def test_reproduces_a_sealed_month_across_a_price_change(
    db_session: Session, org: uuid.UUID, two_books
):
    month_start, changeover, v1, v2 = two_books

    priced_emit(
        db_session,
        org=org,
        at=month_start + timedelta(days=3),
        quantity=10_000,
        unit_price=V1_MICROS,
        book_id=v1.id,
    )
    priced_emit(
        db_session,
        org=org,
        at=changeover + timedelta(days=2),
        quantity=4_000,
        unit_price=V2_MICROS,
        book_id=v2.id,
    )
    db_session.flush()
    fold_and_seal(db_session, now=datetime.now(timezone.utc))

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    )

    assert preview.sealed is True
    assert preview.fully_reproducible is True
    assert preview.unreproducible_cost_micros == 0

    assert preview.price_book_versions == [1, 2]
    assert len(preview.lines) == 2

    for line in preview.lines:
        assert line.recomputed_cost_micros == line.cost_micros
        assert line.cost_micros == pricing_service.cost_micros(
            line.quantity, line.unit_price_micros
        )

    assert preview.total_cost_micros == 2_000 + 1_400


def test_totals_match_the_sealed_rollups(
    db_session: Session, org: uuid.UUID, two_books
):
    month_start, changeover, v1, v2 = two_books
    priced_emit(
        db_session,
        org=org,
        at=month_start + timedelta(days=4),
        quantity=7_777,
        unit_price=V1_MICROS,
        book_id=v1.id,
    )
    db_session.flush()
    fold_and_seal(db_session, now=datetime.now(timezone.utc))

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    )
    rolled = db_session.execute(
        text(
            "SELECT COALESCE(sum(cost_micros), 0) FROM usage_rollups "
            "WHERE organization_id = :o AND grain = 'ORG_TOTAL' "
            "AND granularity = 'MONTH' AND event_type = '*' "
            "AND bucket_start = :b"
        ),
        {"o": org, "b": month_start},
    ).scalar_one()

    assert preview.total_cost_micros == int(rolled)


def test_reproduction_is_stable_on_repeat(
    db_session: Session, org: uuid.UUID, two_books
):
    month_start, _, v1, _ = two_books
    priced_emit(
        db_session,
        org=org,
        at=month_start + timedelta(days=5),
        quantity=1_234,
        unit_price=V1_MICROS,
        book_id=v1.id,
    )
    db_session.flush()
    fold_and_seal(db_session, now=datetime.now(timezone.utc))

    first = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    ).as_dict()
    second = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    ).as_dict()
    assert first == second


def test_eleven_month_old_invoice_still_reproduces(
    db_session: Session, org: uuid.UUID
):
    now = datetime.now(timezone.utc)
    eleven_back = rollup_service.month_bucket(now)
    for _ in range(REPRODUCTION_MONTHS):
        eleven_back = rollup_service.month_bucket(eleven_back - timedelta(days=1))

    book = pricing_service.publish(
        db_session,
        version=1,
        effective_from=eleven_back - timedelta(days=1),
        entries=_entries(V1_MICROS),
    )
    db_session.flush()
    pricing_service.clear_cache()

    priced_emit(
        db_session,
        org=org,
        at=eleven_back + timedelta(days=9),
        quantity=50_000,
        unit_price=V1_MICROS,
        book_id=book.id,
    )
    db_session.flush()
    fold_and_seal(db_session, now=now)

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=eleven_back
    )
    assert preview.sealed is True
    assert preview.fully_reproducible is True
    assert preview.total_cost_micros == 10_000
    assert preview.price_book_versions == [1]


def test_history_report_covers_the_horizon(db_session: Session, org: uuid.UUID):
    now = datetime.now(timezone.utc)
    report = invoice_preview_service.reproduce_history(
        db_session, organization_id=org, months=REPRODUCTION_MONTHS, now=now
    )
    assert len(report.previews) == REPRODUCTION_MONTHS
    assert report.all_sealed_reproducible is True
    assert report.total_unreproducible_micros == 0

    starts = [preview.period_start for preview in report.previews]
    assert len(set(starts)) == REPRODUCTION_MONTHS
    assert all(start < now for start in starts)


def test_legacy_rows_are_reported_not_dropped(db_session: Session, org: uuid.UUID):
    now = datetime.now(timezone.utc)
    month_start = rollup_service.month_bucket(now - timedelta(days=60))

    emit(
        db_session,
        org=org,
        occurred_at=month_start + timedelta(days=3),
        quantity=1_000,
        cost=999,
        model=MODEL,
    )
    db_session.flush()
    fold_and_seal(db_session, now=now)

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    )

    assert preview.fully_reproducible is False
    assert preview.total_cost_micros == 999
    assert preview.reproducible_cost_micros == 0
    assert preview.unreproducible_cost_micros == 999
    assert [line.reason for line in preview.failures()] == [NOT_REPRODUCIBLE_LEGACY]


def test_open_period_is_marked_unsealed(db_session: Session, org: uuid.UUID):
    now = datetime.now(timezone.utc)
    book = pricing_service.publish(
        db_session,
        version=1,
        effective_from=rollup_service.month_bucket(now) - timedelta(days=1),
        entries=_entries(V1_MICROS),
    )
    db_session.flush()
    pricing_service.clear_cache()

    priced_emit(
        db_session,
        org=org,
        at=now - timedelta(hours=2),
        quantity=500,
        unit_price=V1_MICROS,
        book_id=book.id,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=now)
    db_session.flush()

    preview = invoice_preview_service.reproduce(
        db_session,
        organization_id=org,
        period_start=rollup_service.month_bucket(now),
    )
    assert preview.sealed is False
    assert preview.fully_reproducible is True


def test_arithmetic_mismatch_is_caught(db_session: Session, org: uuid.UUID):
    now = datetime.now(timezone.utc)
    month_start = rollup_service.month_bucket(now - timedelta(days=60))
    book = pricing_service.publish(
        db_session,
        version=1,
        effective_from=month_start - timedelta(days=1),
        entries=_entries(V1_MICROS),
    )
    db_session.flush()
    pricing_service.clear_cache()

    priced_emit(
        db_session,
        org=org,
        at=month_start + timedelta(days=2),
        quantity=1_000,
        unit_price=V1_MICROS,
        book_id=book.id,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=now)
    db_session.flush()

    db_session.execute(
        text(
            "UPDATE usage_rollups SET cost_micros = cost_micros + 7 "
            "WHERE organization_id = :o AND grain = 'DETAIL' "
            "AND granularity = 'MONTH' AND sealed_at IS NULL"
        ),
        {"o": org},
    )
    db_session.flush()

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    )
    assert preview.fully_reproducible is False
    assert any(line.reason == "arithmetic_mismatch" for line in preview.failures())


def test_overage_lines_reproduce_like_any_other(
    db_session: Session, org: uuid.UUID
):
    now = datetime.now(timezone.utc)
    month_start = rollup_service.month_bucket(now - timedelta(days=60))
    book = pricing_service.publish(
        db_session,
        version=1,
        effective_from=month_start - timedelta(days=1),
        entries=_entries(V1_MICROS)
        + [
            PriceSpec(
                event_type="llm.input_token.overage",
                provider="groq",
                model=MODEL,
                tier_key="overage",
                unit_price_micros=Decimal("0.500000000"),
            )
        ],
    )
    db_session.flush()
    pricing_service.clear_cache()

    priced_emit(
        db_session,
        org=org,
        at=month_start + timedelta(days=6),
        quantity=200,
        unit_price=Decimal("0.500000000"),
        book_id=book.id,
        event_type="llm.input_token.overage",
    )
    db_session.flush()
    fold_and_seal(db_session, now=now)

    preview = invoice_preview_service.reproduce(
        db_session, organization_id=org, period_start=month_start
    )
    overage = [line for line in preview.lines if line.is_overage]
    assert len(overage) == 1
    assert overage[0].reproducible is True
    assert overage[0].cost_micros == 100


def test_ai_settings_cost_columns_are_gone(db_session: Session):
    columns = {
        column["name"]
        for column in inspect(db_session.get_bind()).get_columns("ai_settings")
    }
    assert "input_cost_per_1k_tokens" not in columns
    assert "output_cost_per_1k_tokens" not in columns


def test_ai_settings_schema_no_longer_declares_them():
    from app.schemas.ai_settings import AISettingsBase, AISettingsResponse

    for model in (AISettingsBase, AISettingsResponse):
        assert "input_cost_per_1k_tokens" not in model.model_fields
        assert "output_cost_per_1k_tokens" not in model.model_fields


def test_no_application_module_names_the_dropped_columns():
    import scripts.verify_arch14 as verify

    assert verify.TENANT_COST_ALLOWLIST == {}
    check = verify.check_no_tenant_cost_reads()
    assert check.status == verify.PASS, check.findings