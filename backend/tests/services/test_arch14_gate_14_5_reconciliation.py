"""Gate 14.5 — provider reconciliation and the six-category drift taxonomy."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, InternalError
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.models.reconciliation import (
    CATEGORY_ORDER,
    Attribution,
    FindingSeverity,
    ProviderStatement,
    ReconciliationCategory,
    ReconciliationFinding,
    ReconciliationStatus,
    StatementGrain,
)
from app.services import rollup_service
from app.services.reconciliation import (
    GeminiBigQuerySource,
    GroqStatementSource,
    ReconciliationRefused,
    StatementPayload,
    StatementLineSpec,
    engine,
    registered_sources,
    source_for,
)
from tests.services.test_arch14_gate_14_2_rollups import emit

pytestmark = pytest.mark.usefixtures("test_database")

MODEL = "llama-3.3-70b-versatile"
INPUT = "llm.input_token"

PERIOD_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 6, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


@pytest.fixture()
def org(db_session: Session) -> uuid.UUID:
    organization = Organization(
        slug=f"recon-{uuid.uuid4().hex[:8]}",
        name="Recon Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(organization)
    db_session.flush()
    return organization.id


def seed_ledger(
    db: Session,
    *,
    org: uuid.UUID,
    quantity: int,
    cost: int,
    at: datetime,
    estimated: bool = False,
    count: int = 1,
) -> None:
    for index in range(count):
        emit(
            db,
            org=org,
            occurred_at=at + timedelta(minutes=index),
            event_type=INPUT,
            quantity=quantity,
            cost=cost,
            provider="groq",
            model=MODEL,
            estimated=estimated,
        )
    db.flush()
    rollup_service.run_rollup(db, now=at + timedelta(hours=1))
    db.flush()


def make_statement(
    db: Session,
    *,
    quantity: Decimal | int | None,
    cost_micros: int,
    source_key: str | None = None,
    extra_lines: tuple[StatementLineSpec, ...] = (),
) -> ProviderStatement:
    lines = (
        StatementLineSpec(
            cost_micros=cost_micros,
            sku="llama-3.3-70b input",
            model=MODEL,
            event_type=INPUT,
            occurred_on=date(2026, 5, 15),
            quantity=Decimal(quantity) if quantity is not None else None,
            unit="token" if quantity is not None else None,
        ),
    ) + extra_lines

    payload = StatementPayload(
        provider="groq",
        source_key=source_key or f"test:{uuid.uuid4().hex[:12]}",
        grain=StatementGrain.DAY,
        attribution=Attribution.ALLOCATED,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        lines=lines,
        source_digest=uuid.uuid4().hex,
        details={"fidelity_note": "test fixture"},
    )
    statement = engine.persist_statement(db, payload)
    db.flush()
    return statement


def run_for(db: Session, statement: ProviderStatement, **kwargs):
    run = engine.reconcile(
        db,
        provider="groq",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        statement=statement,
        now=NOW,
        **kwargs,
    )
    db.flush()
    return run


def categories(run) -> dict[str, int]:
    return {
        finding.category: finding.drift_micros
        for finding in run.findings
    }


def _expect_refusal():
    return pytest.raises((DBAPIError, InternalError))


# ---------------------------------------------------------------------------
# The partition invariant
# ---------------------------------------------------------------------------


def test_categories_partition_the_drift(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=10),
        count=5,
    )
    statement = make_statement(db_session, quantity=5_400, cost_micros=1_180)
    run = run_for(db_session, statement)

    assert run.status == ReconciliationStatus.COMPLETED.value
    assert sum(f.drift_micros for f in run.findings) == run.drift_micros
    assert run.drift_micros == run.statement_cost_micros - run.ledger_cost_micros


def test_every_category_is_a_known_member_of_the_order(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session, org=org, quantity=100, cost=20, at=PERIOD_START + timedelta(days=5)
    )
    statement = make_statement(db_session, quantity=140, cost_micros=45)
    run = run_for(db_session, statement)

    known = {category.value for category in CATEGORY_ORDER}
    assert {finding.category for finding in run.findings} <= known
    assert len(CATEGORY_ORDER) == 6


# ---------------------------------------------------------------------------
# Injected categories
# ---------------------------------------------------------------------------


def test_timing_boundary(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_END - timedelta(hours=2),
        count=3,
    )
    statement = make_statement(db_session, quantity=3_500, cost_micros=700)
    run = run_for(db_session, statement, boundary_hours=6)

    found = categories(run)
    assert ReconciliationCategory.TIMING_BOUNDARY.value in found
    boundary = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.TIMING_BOUNDARY.value
    )
    assert boundary.severity == FindingSeverity.INFO.value
    assert boundary.details["is_upper_bound"] is True
    assert abs(boundary.drift_micros) <= boundary.details["boundary_exposure_micros"]


def test_estimate_drift(db_session: Session, org: uuid.UUID):
    mid = PERIOD_START + timedelta(days=12)
    seed_ledger(db_session, org=org, quantity=1_000, cost=200, at=mid, estimated=True)
    seed_ledger(
        db_session, org=org, quantity=1_000, cost=200, at=mid + timedelta(hours=3)
    )

    statement = make_statement(db_session, quantity=2_300, cost_micros=460)
    run = run_for(db_session, statement, boundary_hours=0)

    found = categories(run)
    assert ReconciliationCategory.ESTIMATE_DRIFT.value in found
    estimate = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.ESTIMATE_DRIFT.value
    )
    assert estimate.details["is_upper_bound"] is True
    assert estimate.details["estimated_quantity"] == "1000.000000"


def test_price_drift(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=10_000,
        cost=2_000,
        at=PERIOD_START + timedelta(days=8),
    )
    statement = make_statement(db_session, quantity=10_000, cost_micros=2_500)
    run = run_for(db_session, statement, boundary_hours=0)

    price = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.PRICE_DRIFT.value
    )
    assert price.drift_micros == 500
    assert price.details["is_upper_bound"] is False
    assert price.details["book_rate_micros_per_unit"].startswith("0.2")
    assert price.details["provider_rate_micros_per_unit"].startswith("0.25")
    assert ReconciliationCategory.UNMETERED_GENERATION.value not in categories(run)


def test_unmetered_generation_is_critical_and_alerts(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=9),
    )
    statement = make_statement(db_session, quantity=5_000, cost_micros=1_000)
    run = run_for(db_session, statement, boundary_hours=0)

    unmetered = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.UNMETERED_GENERATION.value
    )
    assert unmetered.severity == FindingSeverity.CRITICAL.value
    assert unmetered.drift_micros > 0
    assert run.alert_raised is True


def test_unmetered_alerts_even_when_tiny(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000_000,
        cost=200_000,
        at=PERIOD_START + timedelta(days=3),
    )
    statement = make_statement(db_session, quantity=1_000_005, cost_micros=200_001)
    run = run_for(db_session, statement, boundary_hours=0)

    assert abs(run.drift_bps) < 50
    assert any(
        f.category == ReconciliationCategory.UNMETERED_GENERATION.value
        for f in run.findings
    )
    assert run.alert_raised is True


def test_overmetered_ledger(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=5_000,
        cost=1_000,
        at=PERIOD_START + timedelta(days=7),
    )
    statement = make_statement(db_session, quantity=1_000, cost_micros=200)
    run = run_for(db_session, statement, boundary_hours=0)

    over = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.OVERMETERED_LEDGER.value
    )
    assert over.drift_micros < 0
    assert over.severity == FindingSeverity.HIGH.value


def test_unexplained_when_statement_has_no_quantity(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=4),
    )
    statement = make_statement(db_session, quantity=None, cost_micros=350)
    run = run_for(db_session, statement, boundary_hours=0)

    unexplained = next(
        f
        for f in run.findings
        if f.category == ReconciliationCategory.UNEXPLAINED.value
    )
    assert unexplained.details["statement_quantity_available"] is False
    assert unexplained.drift_micros == 150


def test_unmapped_sku_lands_in_unexplained_with_the_sku_named(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=6),
    )
    statement = make_statement(
        db_session,
        quantity=1_000,
        cost_micros=200,
        extra_lines=(
            StatementLineSpec(
                cost_micros=9_000,
                sku="llama-4-something-new input",
                model=None,
                quantity=Decimal(45_000),
            ),
        ),
    )
    run = run_for(db_session, statement, boundary_hours=0)

    unexplained = [
        f
        for f in run.findings
        if f.category == ReconciliationCategory.UNEXPLAINED.value
    ]
    assert any(
        f.details and "llama-4-something-new input" in (f.details.get("skus") or [])
        for f in unexplained
    )


def test_consuming_order_puts_benign_explanations_first(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_END - timedelta(hours=1),
        count=10,
    )
    statement = make_statement(db_session, quantity=10_500, cost_micros=2_100)
    run = run_for(db_session, statement, boundary_hours=6)

    found = categories(run)
    assert ReconciliationCategory.TIMING_BOUNDARY.value in found
    assert ReconciliationCategory.UNMETERED_GENERATION.value not in found
    assert run.alert_raised is False


# ---------------------------------------------------------------------------
# The ledger is not touched
# ---------------------------------------------------------------------------


def test_ledger_is_byte_identical_after_a_run(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=2),
        count=4,
    )

    def snapshot():
        return db_session.execute(
            text(
                "SELECT md5(string_agg(t::text, '|' ORDER BY t.seq)) "
                "FROM (SELECT * FROM usage_events WHERE organization_id = :o) t"
            ),
            {"o": org},
        ).scalar_one()

    before = snapshot()
    statement = make_statement(db_session, quantity=9_999, cost_micros=99_999)
    run_for(db_session, statement, boundary_hours=0)
    assert snapshot() == before


def test_rollups_are_untouched_too(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=2),
    )
    before = db_session.execute(
        text(
            "SELECT sum(cost_micros), sum(quantity) FROM usage_rollups "
            "WHERE organization_id = :o"
        ),
        {"o": org},
    ).one()

    statement = make_statement(db_session, quantity=50_000, cost_micros=50_000)
    run_for(db_session, statement, boundary_hours=0)

    after = db_session.execute(
        text(
            "SELECT sum(cost_micros), sum(quantity) FROM usage_rollups "
            "WHERE organization_id = :o"
        ),
        {"o": org},
    ).one()
    assert after == before


# ---------------------------------------------------------------------------
# Fidelity
# ---------------------------------------------------------------------------


def test_both_shipped_sources_declare_grain_and_attribution():
    for provider, source in registered_sources().items():
        assert source.provider == provider
        assert isinstance(source.grain, StatementGrain)
        assert isinstance(source.attribution, Attribution)
        assert source.fidelity_note


def test_both_sources_are_allocated_today():
    assert GroqStatementSource.attribution is Attribution.ALLOCATED
    assert GeminiBigQuerySource.attribution is Attribution.ALLOCATED
    assert GeminiBigQuerySource.labels_available is False


def test_allocated_findings_carry_no_organization(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=11),
    )
    statement = make_statement(db_session, quantity=1_400, cost_micros=280)
    run = run_for(db_session, statement, boundary_hours=0)

    assert run.attribution == Attribution.ALLOCATED.value
    assert all(finding.organization_id is None for finding in run.findings)


def test_pro_rata_allocation_is_marked_unmeasured(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session,
        org=org,
        quantity=1_000,
        cost=200,
        at=PERIOD_START + timedelta(days=11),
    )
    statement = make_statement(db_session, quantity=1_400, cost_micros=280)
    run = run_for(db_session, statement, boundary_hours=0)

    allocation = (run.details or {}).get("allocation") or []
    assert allocation
    assert all(entry["measured"] is False for entry in allocation)
    assert "allocation_warning" in run.details


def test_attested_organization_id_is_rejected_from_an_allocated_source(
    db_session: Session, org: uuid.UUID
):
    payload = StatementPayload(
        provider="groq",
        source_key=f"test:attributed:{uuid.uuid4().hex[:8]}",
        grain=StatementGrain.DAY,
        attribution=Attribution.ALLOCATED,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        lines=(
            StatementLineSpec(
                cost_micros=100, sku="x", model=MODEL, organization_id=org
            ),
        ),
    )
    with pytest.raises(Exception) as exc:
        with db_session.begin_nested():
            engine.persist_statement(db_session, payload)
    assert "organization_id" in str(exc.value)


# ---------------------------------------------------------------------------
# Eligibility and evidence
# ---------------------------------------------------------------------------


def test_period_younger_than_the_window_is_refused(
    db_session: Session, org: uuid.UUID
):
    statement = make_statement(db_session, quantity=100, cost_micros=20)
    with pytest.raises(ReconciliationRefused):
        engine.reconcile(
            db_session,
            provider="groq",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            statement=statement,
            now=PERIOD_END + timedelta(hours=6),
            min_age_days=2,
        )


def test_reimporting_the_same_statement_is_idempotent(
    db_session: Session, org: uuid.UUID
):
    key = f"test:{uuid.uuid4().hex[:12]}"
    digest = uuid.uuid4().hex

    def payload():
        return StatementPayload(
            provider="groq",
            source_key=key,
            grain=StatementGrain.DAY,
            attribution=Attribution.ALLOCATED,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            lines=(StatementLineSpec(cost_micros=500, sku="s", model=MODEL),),
            source_digest=digest,
        )

    first = engine.persist_statement(db_session, payload())
    second = engine.persist_statement(db_session, payload())
    assert first.id == second.id


def test_restatement_under_the_same_key_is_refused(
    db_session: Session, org: uuid.UUID
):
    key = f"test:{uuid.uuid4().hex[:12]}"
    engine.persist_statement(
        db_session,
        StatementPayload(
            provider="groq",
            source_key=key,
            grain=StatementGrain.DAY,
            attribution=Attribution.ALLOCATED,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            lines=(StatementLineSpec(cost_micros=500, sku="s", model=MODEL),),
            source_digest="a" * 64,
        ),
    )
    db_session.flush()

    with pytest.raises(Exception) as exc:
        with db_session.begin_nested():
            engine.persist_statement(
                db_session,
                StatementPayload(
                    provider="groq",
                    source_key=key,
                    grain=StatementGrain.DAY,
                    attribution=Attribution.ALLOCATED,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                    lines=(StatementLineSpec(cost_micros=900, sku="s", model=MODEL),),
                    source_digest="b" * 64,
                ),
            )
    assert "restated" in str(exc.value).lower()


def test_statements_and_findings_are_immutable(
    db_session: Session, org: uuid.UUID
):
    seed_ledger(
        db_session, org=org, quantity=100, cost=20, at=PERIOD_START + timedelta(days=1)
    )
    statement = make_statement(db_session, quantity=140, cost_micros=45)
    run = run_for(db_session, statement, boundary_hours=0)

    with _expect_refusal():
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE provider_statements SET total_cost_micros = 1 WHERE id = :i"),
                {"i": str(statement.id)},
            )

    with _expect_refusal():
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE reconciliation_findings SET drift_micros = 0 "
                    "WHERE reconciliation_run_id = :i"
                ),
                {"i": str(run.id)},
            )


def test_completed_run_cannot_be_edited(db_session: Session, org: uuid.UUID):
    seed_ledger(
        db_session, org=org, quantity=100, cost=20, at=PERIOD_START + timedelta(days=1)
    )
    statement = make_statement(db_session, quantity=140, cost_micros=45)
    run = run_for(db_session, statement, boundary_hours=0)

    with _expect_refusal():
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE reconciliation_runs SET drift_micros = 0 WHERE id = :i"),
                {"i": str(run.id)},
            )


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def test_groq_csv_import():
    csv_text = (
        "Date,Model,Direction,Tokens,Cost\n"
        "2026-05-14,llama-3.3-70b,input,1000000,0.59\n"
        "2026-05-14,llama-3.3-70b,output,250000,0.79\n"
    )
    payload = GroqStatementSource().fetch(
        period_start=PERIOD_START, period_end=PERIOD_END, csv_text=csv_text
    )
    assert len(payload.lines) == 2
    assert payload.total_cost_micros == 590_000 + 790_000
    assert {line.event_type for line in payload.lines} == {
        "llm.input_token",
        "llm.output_token",
    }
    assert all(line.organization_id is None for line in payload.lines)


def test_groq_empty_statement_is_refused():
    with pytest.raises(Exception) as exc:
        GroqStatementSource().fetch(
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            csv_text="Date,Model,Direction,Tokens,Cost\n",
        )
    assert "zero lines" in str(exc.value).lower()


def test_groq_unrecognised_header_is_refused():
    with pytest.raises(Exception):
        GroqStatementSource().fetch(
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            csv_text="alpha,beta\n1,2\n",
        )


def test_gemini_export_rows_map_and_credits_are_already_netted():
    rows = [
        {
            "usage_date": "2026-05-03",
            "sku": "Gemini 2.5 Flash Input",
            "usage_amount": 2_000_000,
            "usage_unit": "tokens",
            "cost": 0.30,
        },
        {
            "usage_date": "2026-05-03",
            "sku": "Some Unmapped SKU",
            "usage_amount": 10,
            "usage_unit": "tokens",
            "cost": 0.05,
        },
    ]
    payload = GeminiBigQuerySource().fetch(
        period_start=PERIOD_START, period_end=PERIOD_END, rows=rows
    )
    assert payload.details["mapped_lines"] == 1
    assert payload.details["unmapped_lines"] == 1
    assert payload.details["credits_included"] is True
    assert payload.total_cost_micros == 300_000 + 50_000


def test_source_registry_resolves_both_providers():
    assert source_for("groq") is GroqStatementSource
    assert source_for("gemini") is GeminiBigQuerySource