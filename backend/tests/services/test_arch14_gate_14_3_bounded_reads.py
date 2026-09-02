"""Gate 14.3 — the bounded spend read (finding B2)."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import SpendLimitExceededError
from app.core.usage_events import TOTAL_COST_KEY
from app.models.organization import Organization, OrganizationStatus
from app.models.spend_limit import SpendLimit, SpendLimitPeriod
from app.services import rollup_service, usage_service
from app.services import spend_control_service as spend
from tests.services.test_arch14_gate_14_2_rollups import emit

pytestmark = pytest.mark.usefixtures("test_database")

EVENT_TYPES = ("llm.input_token", "llm.output_token", "ocr.page")
SEED = 20260821


@pytest.fixture()
def org(db_session: Session) -> uuid.UUID:
    organization = Organization(
        slug=f"bounded-{uuid.uuid4().hex[:8]}",
        name="Bounded Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(organization)
    db_session.flush()
    return organization.id


@pytest.fixture()
def anchor() -> datetime:
    return datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)


def _randomised_ledger(
    db_session: Session, org: uuid.UUID, anchor: datetime, *, count: int
) -> datetime:
    rng = random.Random(SEED)
    earliest = anchor - timedelta(days=10)
    for _ in range(count):
        offset = rng.randrange(0, 10 * 24 * 60)
        emit(
            db_session,
            org=org,
            occurred_at=earliest + timedelta(minutes=offset),
            event_type=rng.choice(EVENT_TYPES),
            quantity=rng.randrange(1, 500),
            cost=rng.randrange(0, 5_000),
            provider=rng.choice(("groq", "gemini")),
            model=rng.choice(("a", "b", "c")),
            estimated=rng.random() < 0.15,
        )
    db_session.flush()
    return earliest


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------


def test_bounded_equals_direct_at_200_random_hours(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    earliest = _randomised_ledger(db_session, org, anchor, count=500)
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    rng = random.Random(SEED + 1)
    span_hours = int((anchor - earliest).total_seconds() // 3600)
    points = [
        rollup_service.hour_bucket(earliest + timedelta(hours=rng.randrange(0, span_hours)))
        for _ in range(196)
    ]
    points += [
        rollup_service.hour_bucket(earliest),
        rollup_service.hour_bucket(anchor),
        rollup_service.hour_bucket(earliest - timedelta(hours=5)),
        rollup_service.hour_bucket(anchor + timedelta(hours=5)),
    ]
    assert len(points) == 200

    for since in points:
        bounded = usage_service.usage_totals_bounded(
            db_session, organization_id=org, since=since, now=anchor
        )
        direct = usage_service.usage_totals(
            db_session, organization_id=org, since=since
        )

        for event_type in EVENT_TYPES:
            got = bounded.get(event_type, (Decimal(0), 0))
            want = direct.get(event_type, (Decimal(0), 0))
            assert got[0] == want[0], f"{event_type} quantity at {since.isoformat()}"
            assert got[1] == want[1], f"{event_type} cost at {since.isoformat()}"


def test_bounded_total_cost_equals_direct(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    earliest = _randomised_ledger(db_session, org, anchor, count=400)
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    rng = random.Random(SEED + 2)
    span_hours = int((anchor - earliest).total_seconds() // 3600)
    for _ in range(50):
        since = rollup_service.hour_bucket(
            earliest + timedelta(hours=rng.randrange(0, span_hours))
        )
        assert usage_service.total_cost_micros_bounded(
            db_session, organization_id=org, since=since, now=anchor
        ) == usage_service.total_cost_micros(
            db_session, organization_id=org, since=since
        )


def test_the_unfolded_tail_is_counted(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    since = rollup_service.hour_bucket(anchor - timedelta(days=1))
    emit(db_session, org=org, occurred_at=anchor - timedelta(hours=5), cost=1_000)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    emit(db_session, org=org, occurred_at=anchor - timedelta(minutes=2), cost=777)
    db_session.flush()

    assert rollup_service.backlog_depth(db_session) == 1
    assert (
        usage_service.total_cost_micros_bounded(
            db_session, organization_id=org, since=since, now=anchor
        )
        == 1_777
    )


def test_late_routed_event_is_counted_exactly_once(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    old = anchor - timedelta(hours=40)
    emit(db_session, org=org, occurred_at=old, cost=100)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    rollup_service.seal_due(db_session, now=anchor, grace_hours=26)
    db_session.flush()

    emit(db_session, org=org, occurred_at=old + timedelta(minutes=1), cost=250)
    db_session.flush()
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    month_start = rollup_service.month_bucket(anchor)
    assert (
        usage_service.total_cost_micros_bounded(
            db_session, organization_id=org, since=month_start, now=anchor
        )
        == 350
    )


def test_unaligned_since_falls_back_to_the_exact_read(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    _randomised_ledger(db_session, org, anchor, count=200)
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    unaligned = anchor - timedelta(days=3, minutes=37)
    assert unaligned.minute != 0

    assert usage_service.total_cost_micros_bounded(
        db_session, organization_id=org, since=unaligned, now=anchor
    ) == usage_service.total_cost_micros(
        db_session, organization_id=org, since=unaligned
    )


def test_kill_switch_restores_the_arch10_read(
    db_session: Session, org: uuid.UUID, anchor: datetime, monkeypatch
):
    _randomised_ledger(db_session, org, anchor, count=150)
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    monkeypatch.setattr(
        usage_service.settings, "SPEND_USE_ROLLUP_READS", False, raising=False
    )
    since = rollup_service.month_bucket(anchor)
    assert usage_service.total_cost_micros_bounded(
        db_session, organization_id=org, since=since, now=anchor
    ) == usage_service.total_cost_micros(
        db_session, organization_id=org, since=since
    )


# ---------------------------------------------------------------------------
# The bound itself
# ---------------------------------------------------------------------------


def test_read_cost_is_flat_as_the_ledger_grows(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    since = rollup_service.month_bucket(anchor)

    def profile_after(count: int):
        _randomised_ledger(db_session, org, anchor, count=count)
        rollup_service.run_rollup(db_session, now=anchor)
        db_session.flush()
        return usage_service.bounded_read_profile(
            db_session, organization_id=org, since=since, now=anchor
        )

    small = profile_after(500)
    large = profile_after(2_000)

    assert large.event_rows == 0
    assert large.rollup_rows <= 744 * len(EVENT_TYPES) + 744
    assert large.total <= small.total * 2


def test_total_cost_read_is_capped_at_hours_in_the_period(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    _randomised_ledger(db_session, org, anchor, count=800)
    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    profile = usage_service.bounded_read_profile(
        db_session,
        organization_id=org,
        since=rollup_service.month_bucket(anchor),
        event_type="*",
        now=anchor,
    )
    assert profile.rollup_rows <= 744
    assert profile.event_rows == 0


def test_backlog_is_the_only_thing_that_grows_the_tail(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    since = rollup_service.month_bucket(anchor)
    _randomised_ledger(db_session, org, anchor, count=1_000)

    stalled = usage_service.bounded_read_profile(
        db_session, organization_id=org, since=since, now=anchor
    )
    assert stalled.event_rows == 1_000

    rollup_service.run_rollup(db_session, now=anchor)
    db_session.flush()

    drained = usage_service.bounded_read_profile(
        db_session, organization_id=org, since=since, now=anchor
    )
    assert drained.event_rows == 0
    assert drained.total < stalled.total


# ---------------------------------------------------------------------------
# The ceiling still refuses
# ---------------------------------------------------------------------------


def test_spend_ceiling_refuses_through_the_bounded_path(
    db_session: Session, org: uuid.UUID, anchor: datetime
):
    emit(
        db_session,
        org=org,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        quantity=100,
        cost=9_000,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.flush()

    db_session.add(
        SpendLimit(
            organization_id=org,
            limit_key=TOTAL_COST_KEY,
            period=SpendLimitPeriod.MONTH,
            max_cost_micros=9_500,
            hard_stop=True,
        )
    )
    db_session.flush()

    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db_session,
            organization_id=org,
            event_type="llm.input_token",
            quantity=10,
            cost_micros=1_000,
        )


def test_ceiling_sees_usage_still_sitting_in_the_backlog(
    db_session: Session, org: uuid.UUID
):
    emit(
        db_session,
        org=org,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        quantity=100,
        cost=9_000,
    )
    db_session.flush()
    assert rollup_service.backlog_depth(db_session) == 1

    db_session.add(
        SpendLimit(
            organization_id=org,
            limit_key=TOTAL_COST_KEY,
            period=SpendLimitPeriod.MONTH,
            max_cost_micros=9_500,
            hard_stop=True,
        )
    )
    db_session.flush()

    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db_session,
            organization_id=org,
            event_type="llm.input_token",
            quantity=10,
            cost_micros=1_000,
        )
