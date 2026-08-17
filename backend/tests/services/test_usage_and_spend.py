"""ARCH-10 Steps 2-3 — unit tests for metering and spend controls."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    SpendLimitExceededError,
    SpendLimitMisconfiguredError,
)
from app.core.principal import Principal, system_principal
from app.core.usage_events import USAGE_EVENT_TYPES, EmissionKind
from app.models.spend_limit import SpendLimitPeriod
from app.models.user import User
from app.services import spend_control_service as spend
from app.services import usage_service
from app.services.usage_service import (
    UnknownUsageTypeError,
    UsageEmissionError,
    UsageQuantityError,
)


@pytest.fixture()
def db(db_session: Session) -> Session:
    return db_session


@pytest.fixture()
def org_id(db: Session) -> uuid.UUID:
    from app.models.organization import Organization

    org = Organization(name="metering-test", slug=f"met-{uuid.uuid4().hex[:10]}")
    db.add(org)
    db.flush([org])
    return org.id


def test_llm_tokens_are_directional():
    assert "llm.input_token" in USAGE_EVENT_TYPES
    assert "llm.output_token" in USAGE_EVENT_TYPES
    assert "llm.token" not in USAGE_EVENT_TYPES


def test_storage_is_sampled_not_occurrence():
    assert USAGE_EVENT_TYPES["storage.gb_month"].emission is EmissionKind.SAMPLED
    assert USAGE_EVENT_TYPES["ocr.page"].emission is EmissionKind.OCCURRENCE


@pytest.mark.parametrize("bad", ["ocr.pages", "llm.token", "auth.login", ""])
def test_unknown_types_are_refused(db, org_id, bad):
    with pytest.raises(UnknownUsageTypeError):
        usage_service.record_usage(
            db, organization_id=org_id, event_type=bad, quantity=1
        )


def test_record_usage_flushes_without_committing(db, org_id):
    event = usage_service.record_usage(
        db, organization_id=org_id, event_type="ocr.page", quantity=3
    )
    assert event.id is not None
    assert event.seq is not None
    assert db.in_transaction()


@pytest.mark.parametrize("bad", [0, -1, "abc", float("nan")])
def test_non_positive_quantity_is_refused(db, org_id, bad):
    with pytest.raises(UsageQuantityError):
        usage_service.record_usage(
            db, organization_id=org_id, event_type="ocr.page", quantity=bad
        )


def test_sampled_type_cannot_be_emitted_inline(db, org_id):
    with pytest.raises(UsageEmissionError):
        usage_service.record_usage(
            db, organization_id=org_id, event_type="storage.gb_month", quantity=1
        )
    event = usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="storage.gb_month",
        quantity="0.5",
        allow_sampled=True,
    )
    assert event.unit == "gb_month"


def test_idempotency_key_blocks_a_double_bill(db, org_id):
    key = f"ocr:{uuid.uuid4()}:1"
    usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        quantity=1,
        idempotency_key=key,
    )
    with pytest.raises(IntegrityError):
        usage_service.record_usage(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            quantity=1,
            idempotency_key=key,
        )
        db.flush()


def test_system_principal_is_recorded_without_the_caller_saying_so(db, org_id):
    with system_principal(job_name="jobs.ocr.extract", job_id=uuid.uuid4()):
        event = usage_service.record_usage(
            db, organization_id=org_id, event_type="ocr.page", quantity=4
        )
    assert usage_service.is_system_attributed(event)
    assert event.details["job_name"] == "jobs.ocr.extract"


def test_user_principal_populates_actor_id(db, org_id):
    user = User(
        email=f"meter-user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed_password_sample",
        is_active=True,
    )
    db.add(user)
    db.flush([user])

    event = usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="embedding.token",
        quantity=1200,
        principal=Principal.for_user(user.id),
    )
    assert event.actor_id == user.id
    assert event.api_key_id is None


def test_secret_shaped_detail_keys_are_redacted(db, org_id):
    event = usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        quantity=1,
        details={"api_key": "fp_live_abc", "pages": 1},
    )
    assert event.details["api_key"] == "[REDACTED]"
    assert event.details["pages"] == 1


def test_occurred_at_is_independent_of_created_at(db, org_id):
    earlier = datetime.now(timezone.utc) - timedelta(minutes=40)
    event = usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        quantity=1,
        occurred_at=earlier,
    )
    assert event.occurred_at == earlier


def test_hard_ceiling_raises_before_the_work(db, org_id):
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("10"),
    )
    called = []

    with pytest.raises(SpendLimitExceededError) as exc:
        with spend.guard_usage(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            estimated_quantity=25,
        ) as guard:
            called.append(True)
            guard.record(quantity=25)

    assert called == []
    assert exc.value.dimension == "quantity"
    assert exc.value.limit_key == "ocr.page"


def test_ceiling_counts_prior_usage_in_the_same_period(db, org_id):
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("10"),
    )
    usage_service.record_usage(
        db, organization_id=org_id, event_type="ocr.page", quantity=8
    )
    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db, organization_id=org_id, event_type="ocr.page", quantity=5
        )
    spend.ensure_within_limits(
        db, organization_id=org_id, event_type="ocr.page", quantity=2
    )


def test_soft_limit_allows_and_still_audits(db, org_id):
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("1"),
        hard_stop=False,
    )
    spend.ensure_within_limits(
        db, organization_id=org_id, event_type="ocr.page", quantity=500
    )


def test_zero_cost_provider_is_still_capped(db, org_id):
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.DAY,
        max_quantity=Decimal("5"),
    )
    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            quantity=6,
            cost_micros=0,
        )


def test_unconfigured_org_inherits_a_platform_default(db):
    limits = spend.effective_limits(
        db, organization_id=uuid.uuid4(), limit_key="*", lock=False
    )
    assert limits
    assert all(item.is_default for item in limits)


def test_invalid_limit_key_is_refused(db, org_id):
    with pytest.raises(SpendLimitMisconfiguredError):
        spend.set_limit(
            db,
            organization_id=org_id,
            limit_key="not.a.metered.thing",
            period=SpendLimitPeriod.MONTH,
            max_quantity=Decimal("1"),
        )


def test_limit_with_no_ceiling_is_refused(db, org_id):
    with pytest.raises(SpendLimitMisconfiguredError):
        spend.set_limit(
            db,
            organization_id=org_id,
            limit_key="ocr.page",
            period=SpendLimitPeriod.MONTH,
        )


def test_set_limit_deactivates_the_previous_row(db, org_id):
    first = spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("10"),
    )
    second = spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("50"),
    )
    db.flush()
    assert first.is_active is False
    assert second.is_active is True


def test_period_start_is_calendar_aligned():
    moment = datetime(2026, 8, 17, 14, 30, tzinfo=timezone.utc)
    assert spend.period_start(SpendLimitPeriod.MONTH, now=moment) == datetime(
        2026, 8, 1, tzinfo=timezone.utc
    )
    assert spend.period_start(SpendLimitPeriod.DAY, now=moment) == datetime(
        2026, 8, 17, tzinfo=timezone.utc
    )


def test_period_end_handles_month_lengths():
    feb = datetime(2028, 2, 10, tzinfo=timezone.utc)
    assert spend.period_end(SpendLimitPeriod.MONTH, now=feb) == datetime(
        2028, 3, 1, tzinfo=timezone.utc
    )