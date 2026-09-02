"""Gate 14.2 — rollups via claim-by-marking, and sealing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InternalError
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.services import rollup_service
from app.services.rollup_service import DAY, HOUR, MONTH

pytestmark = pytest.mark.usefixtures("test_database")

_UNITS = {
    "llm.input_token": "token",
    "llm.output_token": "token",
    "ocr.page": "page",
}

_INSERT = text(
    """
    INSERT INTO usage_events (
        organization_id, workspace_id, event_type, unit, quantity,
        cost_micros, provider, details, occurred_at
    )
    VALUES (
        :org, :ws, :event_type, :unit, :quantity,
        :cost, :provider,
        jsonb_build_object('model', :model, 'estimated', :estimated,
                           'price_source', 'legacy_ai_settings'),
        :occurred_at
    )
    RETURNING id, seq
    """
)


def emit(
    db: Session,
    *,
    org: uuid.UUID,
    occurred_at: datetime,
    event_type: str = "llm.input_token",
    quantity: int = 100,
    cost: int = 250,
    provider: str = "groq",
    model: str = "llama-3.3-70b",
    estimated: bool = False,
    workspace_id: Optional[uuid.UUID] = None,
) -> tuple[uuid.UUID, int]:
    row = db.execute(
        _INSERT,
        {
            "org": org,
            "ws": workspace_id,
            "event_type": event_type,
            "unit": _UNITS[event_type],
            "quantity": Decimal(quantity),
            "cost": cost,
            "provider": provider,
            "model": model,
            "estimated": estimated,
            "occurred_at": occurred_at,
        },
    ).one()
    return row[0], row[1]


def direct_sum(
    db: Session, *, org: uuid.UUID, event_type: Optional[str] = None
) -> tuple[Decimal, int]:
    sql = (
        "SELECT COALESCE(sum(quantity), 0), COALESCE(sum(cost_micros), 0) "
        "FROM usage_events WHERE organization_id = :org"
    )
    params: dict = {"org": org}
    if event_type is not None:
        sql += " AND event_type = :t"
        params["t"] = event_type
    row = db.execute(text(sql), params).one()
    return Decimal(row[0]), int(row[1])


def rollup_sum(
    db: Session,
    *,
    org: uuid.UUID,
    granularity: str = HOUR,
    grain: str = "ORG_TOTAL",
    event_type: Optional[str] = None,
) -> tuple[Decimal, int]:
    sql = (
        "SELECT COALESCE(sum(quantity), 0), COALESCE(sum(cost_micros), 0) "
        "FROM usage_rollups WHERE organization_id = :org "
        "AND granularity = :g AND grain = :grain"
    )
    params: dict = {"org": org, "g": granularity, "grain": grain}
    if event_type is not None:
        sql += " AND event_type = :t"
        params["t"] = event_type
    else:
        sql += " AND event_type <> '*'"
    row = db.execute(text(sql), params).one()
    return Decimal(row[0]), int(row[1])


@pytest.fixture()
def org(db_session: Session) -> uuid.UUID:
    organization = Organization(
        slug=f"rollup-{uuid.uuid4().hex[:8]}",
        name="Rollup Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(organization)
    db_session.flush()
    return organization.id


@pytest.fixture()
def anchor() -> datetime:
    return datetime(2026, 6, 15, 14, 30, tzinfo=timezone.utc)


def _expect_refusal():
    return pytest.raises((DBAPIError, InternalError))


# ---------------------------------------------------------------------------
# Claim by marking
# ---------------------------------------------------------------------------


def test_fold_matches_direct_sum_across_a_boundary(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    base = anchor - timedelta(hours=3)
    for index in range(600):
        emit(
            db_session,
            org=org,
            occurred_at=base + timedelta(seconds=index * 11),
            quantity=100 + index,
            cost=250 + index,
        )
    db_session.flush()

    result = rollup_service.run_rollup(db_session, batch_size=250, now=anchor)
    db_session.flush()

    assert result.claimed == 600
    assert result.folded == 600
    assert result.late == 0
    assert rollup_service.backlog_depth(db_session) == 0

    assert rollup_sum(db_session, org=org) == direct_sum(db_session, org=org)

    buckets = db_session.execute(
        text(
            "SELECT count(*) FROM usage_rollups WHERE organization_id = :o "
            "AND granularity = 'HOUR' AND grain = 'ORG_TOTAL' "
            "AND event_type <> '*'"
        ),
        {"o": org},
    ).scalar_one()
    assert buckets >= 2


def test_late_committing_row_is_folded_on_next_pass(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    base = anchor - timedelta(hours=2)
    delayed_id, delayed_seq = emit(
        db_session, org=org, occurred_at=base, quantity=1_000, cost=9_999
    )
    # Simulate an in-flight transaction holding the row during pass 1
    db_session.execute(
        text("UPDATE usage_events SET aggregated_at = '2099-01-01T00:00:00Z' WHERE id = :i"),
        {"i": delayed_id},
    )
    for index in range(20):
        emit(
            db_session,
            org=org,
            occurred_at=base + timedelta(minutes=index),
            quantity=10,
            cost=20,
        )
    db_session.flush()

    # Pass 1 folds only the 20 newer events
    rollup_service.run_rollup(db_session, now=anchor)

    # Delayed transaction commits (aggregated_at reset to NULL)
    db_session.execute(
        text("UPDATE usage_events SET aggregated_at = NULL WHERE id = :i"),
        {"i": delayed_id},
    )
    db_session.flush()

    highest_folded = db_session.execute(
        text(
            "SELECT max(seq) FROM usage_events "
            "WHERE organization_id = :o AND aggregated_at IS NOT NULL"
        ),
        {"o": org},
    ).scalar_one()
    assert delayed_seq < highest_folded, "the row must be *below* the watermark"

    second = rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    assert second.claimed == 1
    assert rollup_service.backlog_depth(db_session) == 0

    assert rollup_sum(db_session, org=org) == direct_sum(db_session, org=org)


def test_claim_predicate_is_marking_not_a_watermark(db_session: Session):
    sql = str(rollup_service._CLAIM_SQL).lower()
    assert "aggregated_at is null" in sql
    assert "for update skip locked" in sql
    assert "> :watermark" not in sql
    assert "last_seq" not in sql


def test_rerun_folds_nothing_twice(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    for index in range(50):
        emit(
            db_session,
            org=org,
            occurred_at=anchor - timedelta(minutes=index + 90),
        )
    db_session.flush()

    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()
    before = rollup_sum(db_session, org=org)

    for _ in range(3):
        again = rollup_service.run_rollup(db_session, now=anchor)
        assert again.claimed == 0
    db_session.flush()

    assert rollup_sum(db_session, org=org) == before
    assert before == direct_sum(db_session, org=org)


# ---------------------------------------------------------------------------
# Grains and granularities
# ---------------------------------------------------------------------------


def test_day_and_month_are_derived_from_hour(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    for index in range(120):
        emit(
            db_session,
            org=org,
            occurred_at=anchor - timedelta(minutes=index * 7),
            quantity=13,
            cost=29,
        )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    hourly = rollup_sum(db_session, org=org, granularity=HOUR)
    daily = rollup_sum(db_session, org=org, granularity=DAY)
    monthly = rollup_sum(db_session, org=org, granularity=MONTH)

    assert hourly == daily == monthly == direct_sum(db_session, org=org)


def test_org_total_grain_equals_detail_grain(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    workspace_a, workspace_b = None, None
    for index in range(40):
        emit(
            db_session,
            org=org,
            occurred_at=anchor - timedelta(minutes=index * 3),
            provider="groq" if index % 2 else "gemini",
            model=f"model-{index % 4}",
            workspace_id=workspace_a if index % 2 else workspace_b,
        )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    detail = rollup_sum(db_session, org=org, grain="DETAIL")
    org_total = rollup_sum(db_session, org=org, grain="ORG_TOTAL")
    assert detail == org_total == direct_sum(db_session, org=org)


def test_wildcard_row_carries_total_cost_across_types(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    emit(db_session, org=org, occurred_at=anchor - timedelta(minutes=5), cost=100)
    emit(
        db_session,
        org=org,
        occurred_at=anchor - timedelta(minutes=6),
        event_type="llm.output_token",
        cost=200,
    )
    emit(
        db_session,
        org=org,
        occurred_at=anchor - timedelta(minutes=7),
        event_type="ocr.page",
        quantity=3,
        cost=300,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    wildcard_cost = db_session.execute(
        text(
            "SELECT COALESCE(sum(cost_micros), 0) FROM usage_rollups "
            "WHERE organization_id = :o AND grain = 'ORG_TOTAL' "
            "AND granularity = 'HOUR' AND event_type = '*'"
        ),
        {"o": org},
    ).scalar_one()

    assert int(wildcard_cost) == 600
    assert int(wildcard_cost) == direct_sum(db_session, org=org)[1]


def test_estimated_usage_is_tracked_separately(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    for index in range(10):
        emit(
            db_session,
            org=org,
            occurred_at=anchor - timedelta(minutes=index + 70),
            quantity=100,
            cost=200,
            estimated=index < 4,
        )
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    row = db_session.execute(
        text(
            "SELECT sum(quantity), sum(estimated_quantity), "
            "       sum(cost_micros), sum(estimated_cost_micros), "
            "       sum(estimated_event_count) "
            "FROM usage_rollups WHERE organization_id = :o "
            "AND granularity = 'HOUR' AND grain = 'ORG_TOTAL' "
            "AND event_type = 'llm.input_token'"
        ),
        {"o": org},
    ).one()

    assert Decimal(row[0]) == Decimal(1_000)
    assert Decimal(row[1]) == Decimal(400)
    assert int(row[2]) == 2_000
    assert int(row[3]) == 800
    assert int(row[4]) == 4


def test_tenants_do_not_mix(db_session: Session, anchor: datetime):
    first = Organization(
        slug=f"a-{uuid.uuid4().hex[:8]}", name="A", status=OrganizationStatus.ACTIVE
    )
    second = Organization(
        slug=f"b-{uuid.uuid4().hex[:8]}", name="B", status=OrganizationStatus.ACTIVE
    )
    db_session.add_all([first, second])
    db_session.flush()

    emit(db_session, org=first.id, occurred_at=anchor - timedelta(minutes=5), cost=111)
    emit(db_session, org=second.id, occurred_at=anchor - timedelta(minutes=5), cost=222)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    assert rollup_sum(db_session, org=first.id)[1] == 111
    assert rollup_sum(db_session, org=second.id)[1] == 222


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


def test_seal_closes_windows_past_the_grace_period(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    outcome = rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    sealed_hours = [s for g, s in outcome.sealed if g == HOUR]
    assert rollup_service.hour_bucket(old) in sealed_hours

    sealed_rows = db_session.execute(
        text(
            "SELECT count(*) FROM usage_rollups WHERE organization_id = :o "
            "AND granularity = 'HOUR' AND sealed_at IS NOT NULL"
        ),
        {"o": org},
    ).scalar_one()
    assert sealed_rows > 0


def test_seal_respects_the_grace_window(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    recent = anchor - timedelta(hours=3)
    emit(db_session, org=org, occurred_at=recent)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    outcome = rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    assert rollup_service.hour_bucket(recent) not in [s for _, s in outcome.sealed]


def test_seal_refuses_over_an_unfolded_backlog(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    emit(db_session, org=org, occurred_at=old + timedelta(minutes=1))
    db_session.flush()

    outcome = rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    reasons = [why for _, _, why in outcome.skipped]
    assert any("unaggregated" in why for why in reasons)
    assert rollup_service.hour_bucket(old) not in [s for _, s in outcome.sealed]


def test_late_event_lands_in_the_open_bucket_with_the_annotation(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    old_hour = rollup_service.hour_bucket(old)

    emit(db_session, org=org, occurred_at=old, quantity=10, cost=10)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    sealed_before = db_session.execute(
        text(
            "SELECT quantity, cost_micros, sealed_at FROM usage_rollups "
            "WHERE organization_id = :o AND granularity = 'HOUR' "
            "AND grain = 'ORG_TOTAL' AND event_type = 'llm.input_token' "
            "AND bucket_start = :b"
        ),
        {"o": org, "b": old_hour},
    ).one()
    assert sealed_before[2] is not None

    emit(db_session, org=org, occurred_at=old + timedelta(minutes=2), quantity=7, cost=70)
    db_session.flush()
    result = rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    assert result.late == 1

    sealed_after = db_session.execute(
        text(
            "SELECT quantity, cost_micros FROM usage_rollups "
            "WHERE organization_id = :o AND granularity = 'HOUR' "
            "AND grain = 'ORG_TOTAL' AND event_type = 'llm.input_token' "
            "AND bucket_start = :b"
        ),
        {"o": org, "b": old_hour},
    ).one()
    assert (sealed_after[0], sealed_after[1]) == (sealed_before[0], sealed_before[1])

    open_hour = rollup_service.hour_bucket(anchor)
    landed = db_session.execute(
        text(
            "SELECT quantity, cost_micros, late_event_count, late_quantity, "
            "       late_cost_micros, details "
            "FROM usage_rollups WHERE organization_id = :o "
            "AND granularity = 'HOUR' AND grain = 'ORG_TOTAL' "
            "AND event_type = 'llm.input_token' AND bucket_start = :b"
        ),
        {"o": org, "b": open_hour},
    ).one()

    assert Decimal(landed[0]) == Decimal(7)
    assert int(landed[1]) == 70
    assert int(landed[2]) == 1
    assert Decimal(landed[3]) == Decimal(7)
    assert int(landed[4]) == 70
    assert landed[5]["late_from_buckets"] == {old_hour.isoformat(): 1}

    assert rollup_sum(db_session, org=org) == direct_sum(db_session, org=org)


def test_sealed_bucket_cannot_be_mutated(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    with _expect_refusal():
        db_session.execute(
            text(
                "UPDATE usage_rollups SET quantity = quantity + 1 "
                "WHERE organization_id = :o AND sealed_at IS NOT NULL"
            ),
            {"o": org},
        )
        db_session.flush()
    db_session.rollback()


def test_sealed_bucket_cannot_be_deleted(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    with _expect_refusal():
        db_session.execute(
            text(
                "DELETE FROM usage_rollups WHERE organization_id = :o "
                "AND sealed_at IS NOT NULL"
            ),
            {"o": org},
        )
        db_session.flush()
    db_session.rollback()


def test_sealed_day_is_not_recomputed_by_a_later_pass(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old, quantity=5, cost=50)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    sealed_day = db_session.execute(
        text(
            "SELECT quantity, cost_micros, sealed_at FROM usage_rollups "
            "WHERE organization_id = :o AND granularity = 'DAY' "
            "AND grain = 'ORG_TOTAL' AND event_type = 'llm.input_token'"
        ),
        {"o": org},
    ).one()
    assert sealed_day[2] is not None

    emit(db_session, org=org, occurred_at=old + timedelta(minutes=1), quantity=99, cost=990)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    after = db_session.execute(
        text(
            "SELECT quantity, cost_micros FROM usage_rollups "
            "WHERE organization_id = :o AND granularity = 'DAY' "
            "AND grain = 'ORG_TOTAL' AND event_type = 'llm.input_token' "
            "AND sealed_at IS NOT NULL"
        ),
        {"o": org},
    ).one()
    assert (after[0], after[1]) == (sealed_day[0], sealed_day[1])


def test_window_rows_record_the_period_state(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old)
    emit(db_session, org=org, occurred_at=old + timedelta(minutes=1))
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    window = db_session.execute(
        text(
            "SELECT status, event_count, first_rolled_at, last_rolled_at "
            "FROM rollup_windows WHERE granularity = 'HOUR' AND bucket_start = :b"
        ),
        {"b": rollup_service.hour_bucket(old)},
    ).one()
    assert window[0] == "OPEN"
    assert window[1] == 2
    assert window[2] is not None and window[3] is not None

    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    status = db_session.execute(
        text(
            "SELECT status, sealed_at FROM rollup_windows "
            "WHERE granularity = 'HOUR' AND bucket_start = :b"
        ),
        {"b": rollup_service.hour_bucket(old)},
    ).one()
    assert status[0] == "SEALED"
    assert status[1] is not None
