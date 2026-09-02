"""Gate 14.1 — the price book, and the B1 bypass closed as a test."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, InternalError
from sqlalchemy.orm import Session

from app.core.exceptions import SpendLimitExceededError
from app.core.usage_events import TOTAL_COST_KEY
from app.models.organization import Organization, OrganizationStatus
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.spend_limit import SpendLimit, SpendLimitPeriod
from app.models.usage_event import UsageEvent
from app.services import llm_metering, pricing_service
from app.services.llm_metering import LLMMeteringError
from app.services.pricing_service import PriceSpec, PriceUnavailableError

pytestmark = pytest.mark.usefixtures("test_database")

INPUT_MICROS = Decimal("0.200000000")
OUTPUT_MICROS = Decimal("0.800000000")


@pytest.fixture(autouse=True)
def _no_price_cache(monkeypatch):
    monkeypatch.setattr(
        pricing_service.settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    pricing_service.clear_cache()
    yield
    pricing_service.clear_cache()


@pytest.fixture()
def org_id(db_session: Session) -> uuid.UUID:
    org = Organization(
        slug=f"pricing-{uuid.uuid4().hex[:8]}",
        name="Pricing Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush()
    return org.id


def _entries() -> list[PriceSpec]:
    return [
        PriceSpec(
            event_type="llm.input_token",
            provider="groq",
            model=None,
            unit_price_micros=INPUT_MICROS,
        ),
        PriceSpec(
            event_type="llm.output_token",
            provider="groq",
            model=None,
            unit_price_micros=OUTPUT_MICROS,
        ),
        PriceSpec(
            event_type="llm.input_token",
            provider="groq",
            model="llama-3.3-70b",
            unit_price_micros=Decimal("0.590000000"),
        ),
        PriceSpec(
            event_type="llm.output_token",
            provider="groq",
            model="llama-3.3-70b",
            unit_price_micros=Decimal("0.790000000"),
        ),
    ]


@pytest.fixture()
def book(db_session: Session) -> PriceBook:
    existing = db_session.execute(
        select(PriceBook).where(PriceBook.version == 1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    published = pricing_service.publish(
        db_session,
        version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        entries=_entries(),
        notes="Gate 14.1 fixture",
    )
    db_session.flush()
    pricing_service.clear_cache()
    return published


def _ai_settings(**overrides):
    base = dict(
        provider=SimpleNamespace(value="GROQ"),
        model="llama-3.3-70b",
        max_output_tokens=512,
        input_cost_per_1k_tokens=0.0,
        output_cost_per_1k_tokens=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _token_usage(prompt: int, completion: int, *, provider="groq", model="llama-3.3-70b"):
    return SimpleNamespace(
        provider=provider,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def _reserve(db: Session, org_id: uuid.UUID, **kwargs):
    return llm_metering.reserve(
        db,
        organization_id=org_id,
        workspace_id=None,
        conversation_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        prompt="x" * 350,
        ai_settings=kwargs.pop("ai_settings", _ai_settings()),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Gate 14.1 — the B1 bypass
# ---------------------------------------------------------------------------


def test_b1_bypass_is_closed(db_session: Session, org_id: uuid.UUID, book: PriceBook):
    settings_with_zero_prices = _ai_settings()
    assert settings_with_zero_prices.input_cost_per_1k_tokens == 0.0
    assert settings_with_zero_prices.output_cost_per_1k_tokens == 0.0

    reservation = _reserve(db_session, org_id, ai_settings=settings_with_zero_prices)
    llm_metering.settle(
        db_session,
        reservation=reservation,
        token_usage=_token_usage(1000, 500),
    )
    db_session.flush()

    rows = {
        row.event_type: row
        for row in db_session.execute(
            select(UsageEvent).where(UsageEvent.organization_id == org_id)
        ).scalars()
    }

    inp = rows["llm.input_token"]
    out = rows["llm.output_token"]

    assert inp.cost_micros == 590, "1000 tokens * 0.59 micros"
    assert out.cost_micros == 395, "500 tokens * 0.79 micros"
    assert inp.cost_micros > 0 and out.cost_micros > 0

    assert inp.price_book_id == book.id
    assert Decimal(str(inp.unit_price_micros)) == Decimal("0.590000000")
    assert inp.cost_micros == pricing_service.cost_micros(
        inp.quantity, inp.unit_price_micros
    )
    assert inp.details["price_source"] == "price_book"
    assert inp.details["price_book_version"] == 1
    assert "price_fallback" not in inp.details


def test_total_cost_ceiling_still_refuses_when_tenant_zeroes_prices(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    db_session.add(
        SpendLimit(
            organization_id=org_id,
            limit_key=TOTAL_COST_KEY,
            period=SpendLimitPeriod.MONTH,
            max_cost_micros=100,
            hard_stop=True,
        )
    )
    db_session.flush()

    with pytest.raises(SpendLimitExceededError):
        _reserve(db_session, org_id, ai_settings=_ai_settings())


def test_quantity_ceiling_unaffected(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    db_session.add(
        SpendLimit(
            organization_id=org_id,
            limit_key="llm.input_token",
            period=SpendLimitPeriod.MONTH,
            max_quantity=Decimal(10),
            hard_stop=True,
        )
    )
    db_session.flush()

    with pytest.raises(SpendLimitExceededError):
        _reserve(db_session, org_id, ai_settings=_ai_settings())


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_unpriced_provider_refuses_rather_than_pricing_at_zero(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    with pytest.raises(LLMMeteringError) as exc:
        _reserve(
            db_session,
            org_id,
            ai_settings=_ai_settings(
                provider=SimpleNamespace(value="ANTHROPIC"), model="claude-opus-5"
            ),
        )
    assert "unpriced" in str(exc.value).lower() or "price" in str(exc.value).lower()

    assert (
        db_session.execute(
            select(UsageEvent).where(UsageEvent.organization_id == org_id)
        ).first()
        is None
    )


def test_model_fallback_is_recorded_not_silent(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    reservation = _reserve(
        db_session,
        org_id,
        ai_settings=_ai_settings(model="llama-4-tomorrow"),
    )
    assert reservation.input_price.fallback is True
    assert reservation.input_price.entry_model is None

    llm_metering.settle(
        db_session,
        reservation=reservation,
        token_usage=_token_usage(1000, 100, model="llama-4-tomorrow"),
    )
    db_session.flush()

    row = db_session.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == org_id,
            UsageEvent.event_type == "llm.input_token",
        )
    ).scalar_one()

    assert row.details["price_fallback"] is True
    assert row.details["price_fallback_from"] == "llama-4-tomorrow"
    assert row.cost_micros == 200


def test_resolution_is_by_occurrence_time_not_by_current(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    changeover = datetime.now(timezone.utc) + timedelta(days=1)
    pricing_service.publish(
        db_session,
        version=2,
        effective_from=changeover,
        entries=[
            PriceSpec(
                event_type="llm.input_token",
                provider="groq",
                model="llama-3.3-70b",
                unit_price_micros=Decimal("1.230000000"),
            ),
            PriceSpec(
                event_type="llm.output_token",
                provider="groq",
                model="llama-3.3-70b",
                unit_price_micros=Decimal("4.560000000"),
            ),
        ],
    )
    db_session.flush()
    pricing_service.clear_cache()

    yesterday = pricing_service.resolve(
        db_session,
        event_type="llm.input_token",
        provider="groq",
        model="llama-3.3-70b",
        at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    tomorrow = pricing_service.resolve(
        db_session,
        event_type="llm.input_token",
        provider="groq",
        model="llama-3.3-70b",
        at=changeover + timedelta(hours=1),
    )

    assert yesterday.price_book_version == 1
    assert yesterday.unit_price_micros == Decimal("0.590000000")
    assert tomorrow.price_book_version == 2
    assert tomorrow.unit_price_micros == Decimal("1.230000000")


def test_no_book_covering_the_instant_refuses(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    with pytest.raises(PriceUnavailableError):
        pricing_service.resolve(
            db_session,
            event_type="llm.input_token",
            provider="groq",
            model="llama-3.3-70b",
            at=datetime.now(timezone.utc) - timedelta(days=365),
        )


def test_publishing_closes_the_predecessor_window(
    db_session: Session, book: PriceBook
):
    changeover = datetime.now(timezone.utc) + timedelta(days=7)
    pricing_service.publish(
        db_session,
        version=2,
        effective_from=changeover,
        entries=_entries(),
    )
    db_session.flush()
    db_session.refresh(book)
    assert book.effective_to == changeover


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def _expect_refusal():
    return pytest.raises((DBAPIError, InternalError, IntegrityError))


def test_published_book_cannot_be_repriced_by_update(
    db_session: Session, book: PriceBook
):
    with _expect_refusal():
        db_session.execute(
            text("UPDATE price_books SET currency = 'EUR' WHERE id = :i"),
            {"i": str(book.id)},
        )
        db_session.flush()
    db_session.rollback()


def test_published_book_entries_cannot_be_updated(
    db_session: Session, book: PriceBook
):
    with _expect_refusal():
        db_session.execute(
            text(
                "UPDATE price_book_entries SET unit_price_micros = 0 "
                "WHERE price_book_id = :i"
            ),
            {"i": str(book.id)},
        )
        db_session.flush()
    db_session.rollback()


def test_published_book_cannot_gain_entries(db_session: Session, book: PriceBook):
    with _expect_refusal():
        db_session.add(
            PriceBookEntry(
                price_book_id=book.id,
                event_type="llm.input_token",
                provider="groq",
                model="smuggled-in",
                unit="token",
                unit_price_micros=Decimal("0"),
            )
        )
        db_session.flush()
    db_session.rollback()


def test_published_book_cannot_be_deleted(db_session: Session, book: PriceBook):
    with _expect_refusal():
        db_session.execute(
            text("DELETE FROM price_books WHERE id = :i"), {"i": str(book.id)}
        )
        db_session.flush()
    db_session.rollback()


def test_closed_window_cannot_be_reopened_or_moved(
    db_session: Session, book: PriceBook
):
    pricing_service.publish(
        db_session,
        version=2,
        effective_from=datetime.now(timezone.utc) + timedelta(days=7),
        entries=_entries(),
    )
    db_session.flush()

    with _expect_refusal():
        db_session.execute(
            text("UPDATE price_books SET effective_to = NULL WHERE id = :i"),
            {"i": str(book.id)},
        )
        db_session.flush()
    db_session.rollback()


def test_usage_event_price_columns_are_immutable(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    reservation = _reserve(db_session, org_id)
    llm_metering.settle(
        db_session, reservation=reservation, token_usage=_token_usage(100, 50)
    )
    db_session.flush()
    row = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id).limit(1)
    ).scalar_one()

    with _expect_refusal():
        db_session.execute(
            text("UPDATE usage_events SET unit_price_micros = 0 WHERE id = :i"),
            {"i": str(row.id)},
        )
        db_session.flush()
    db_session.rollback()


def test_aggregated_at_is_still_updatable(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    reservation = _reserve(db_session, org_id)
    llm_metering.settle(
        db_session, reservation=reservation, token_usage=_token_usage(100, 50)
    )
    db_session.flush()
    row = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id).limit(1)
    ).scalar_one()

    db_session.execute(
        text("UPDATE usage_events SET aggregated_at = now() WHERE id = :i"),
        {"i": str(row.id)},
    )
    db_session.flush()


def test_digest_detects_mutation(db_session: Session, book: PriceBook):
    assert pricing_service.verify_digest(book) is True

    db_session.execute(text("SET session_replication_role = 'replica'"))
    db_session.execute(
        text(
            "UPDATE price_book_entries SET unit_price_micros = 999 "
            "WHERE price_book_id = :i"
        ),
        {"i": str(book.id)},
    )
    db_session.execute(text("SET session_replication_role = 'origin'"))
    db_session.expire_all()

    mutated = db_session.get(PriceBook, book.id)
    assert pricing_service.verify_digest(mutated) is False


# ---------------------------------------------------------------------------
# Validation and arithmetic
# ---------------------------------------------------------------------------


def test_publish_rejects_unknown_event_type(db_session: Session):
    with pytest.raises(pricing_service.PriceBookValidationError):
        pricing_service.publish(
            db_session,
            version=99,
            effective_from=datetime.now(timezone.utc),
            entries=[
                PriceSpec(
                    event_type="llm.telepathy_token",
                    provider="groq",
                    unit_price_micros=Decimal("1"),
                )
            ],
        )
    db_session.rollback()


def test_publish_rejects_unit_mismatch(db_session: Session):
    with pytest.raises(pricing_service.PriceBookValidationError):
        pricing_service.publish(
            db_session,
            version=98,
            effective_from=datetime.now(timezone.utc),
            entries=[
                PriceSpec(
                    event_type="llm.input_token",
                    provider="groq",
                    unit="page",
                    unit_price_micros=Decimal("1"),
                )
            ],
        )
    db_session.rollback()


def test_publish_rejects_duplicate_scope(db_session: Session):
    spec = PriceSpec(
        event_type="llm.input_token",
        provider="groq",
        model=None,
        unit_price_micros=Decimal("1"),
    )
    with pytest.raises(pricing_service.PriceBookValidationError):
        pricing_service.publish(
            db_session,
            version=97,
            effective_from=datetime.now(timezone.utc),
            entries=[spec, spec],
        )
    db_session.rollback()


@pytest.mark.parametrize(
    "quantity,price,expected",
    [
        (1000, "0.590000000", 590),
        (1, "0.500000000", 1),
        (3, "0.500000000", 2),
        (0.5, "1.000000000", 1),
        (1_000_000, "0.000001000", 1),
    ],
)
def test_cost_rounding_matches_postgres(
    db_session: Session, quantity, price, expected
):
    computed = pricing_service.cost_micros(quantity, price)
    from_pg = db_session.execute(
        text("SELECT round(CAST(:q AS numeric) * CAST(:p AS numeric))"),
        {"q": str(quantity), "p": price},
    ).scalar_one()

    assert computed == expected
    assert computed == int(from_pg)


def test_check_constraint_rejects_inconsistent_cost(
    db_session: Session, org_id: uuid.UUID, book: PriceBook
):
    with _expect_refusal():
        db_session.execute(
            text(
                "INSERT INTO usage_events "
                "(organization_id, event_type, unit, quantity, cost_micros, "
                " price_book_id, unit_price_micros, occurred_at) "
                "VALUES (:o, 'llm.input_token', 'token', 100, 999999, "
                "        :b, 0.5, now())"
            ),
            {"o": str(org_id), "b": str(book.id)},
        )
        db_session.flush()
    db_session.rollback()
