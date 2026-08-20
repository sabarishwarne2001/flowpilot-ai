"""Gate 14.4 — quota tiers, three-layer resolution, and overage policy."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, InternalError
from sqlalchemy.orm import Session

from app.core.exceptions import RateLimitExceededError, SpendLimitExceededError
from app.core.usage_events import (
    TOTAL_COST_KEY,
    USAGE_EVENT_TYPES,
    is_overage_type,
    overage_type_for,
)
from app.models.organization import Organization, OrganizationStatus
from app.models.quota_tier import OveragePolicy, QuotaTier, QuotaTierEntry
from app.models.spend_limit import SpendLimit, SpendLimitPeriod
from app.models.usage_event import UsageEvent
from app.services import pricing_service, quota_service, rollup_service
from app.services import spend_control_service as spend
from app.services.pricing_service import PriceSpec
from app.services.quota_service import TierEntrySpec
from tests.services.test_arch14_gate_14_2_rollups import emit

pytestmark = pytest.mark.usefixtures("test_database")

OVERAGE_TIER_KEY = "overage"
STANDARD_MICROS = Decimal("0.200000000")
OVERAGE_MICROS = Decimal("0.500000000")


@pytest.fixture(autouse=True)
def _no_caches(monkeypatch):
    monkeypatch.setattr(
        pricing_service.settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    monkeypatch.setattr(
        quota_service.settings, "QUOTA_TIER_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    pricing_service.clear_cache()
    quota_service.clear_cache()
    yield
    pricing_service.clear_cache()
    quota_service.clear_cache()


@pytest.fixture()
def org(db_session: Session) -> uuid.UUID:
    organization = Organization(
        slug=f"quota-{uuid.uuid4().hex[:8]}",
        name="Quota Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(organization)
    db_session.flush()
    return organization.id


@pytest.fixture()
def book(db_session: Session):
    entries = [
        PriceSpec(
            event_type="llm.input_token",
            provider="groq",
            model=None,
            unit_price_micros=STANDARD_MICROS,
        ),
        PriceSpec(
            event_type="llm.output_token",
            provider="groq",
            model=None,
            unit_price_micros=STANDARD_MICROS,
        ),
        PriceSpec(
            event_type="llm.input_token.overage",
            provider="groq",
            model=None,
            tier_key=OVERAGE_TIER_KEY,
            unit_price_micros=OVERAGE_MICROS,
        ),
        PriceSpec(
            event_type="llm.output_token.overage",
            provider="groq",
            model=None,
            tier_key=OVERAGE_TIER_KEY,
            unit_price_micros=OVERAGE_MICROS,
        ),
    ]
    published = pricing_service.publish(
        db_session,
        version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        entries=entries,
    )
    db_session.flush()
    pricing_service.clear_cache()
    return published


def _publish_tier(
    db: Session,
    *,
    key: str = "business",
    version: int = 1,
    max_quantity: str = "1000",
    policy: str = OveragePolicy.REFUSE.value,
    grace: str | None = None,
    limit_key: str = "llm.input_token",
    effective_from: datetime | None = None,
) -> QuotaTier:
    tier = quota_service.publish_tier(
        db,
        key=key,
        display_name=key.title(),
        version=version,
        effective_from=effective_from or (datetime.now(timezone.utc) - timedelta(days=7)),
        entries=[
            TierEntrySpec(
                limit_key=limit_key,
                period=SpendLimitPeriod.MONTH,
                max_quantity=Decimal(max_quantity),
                overage_policy=policy,
                overage_price_tier_key=(
                    OVERAGE_TIER_KEY
                    if policy == OveragePolicy.ALLOW_AND_BILL.value
                    else None
                ),
                grace_quantity=Decimal(grace) if grace else None,
            )
        ],
    )
    db.flush()
    quota_service.clear_cache()
    return tier


def _expect_refusal():
    return pytest.raises((DBAPIError, InternalError))


# ---------------------------------------------------------------------------
# The gate's first half — resolution order
# ---------------------------------------------------------------------------


def test_resolution_order(db_session: Session, org: uuid.UUID, book):
    tier = _publish_tier(db_session, max_quantity="1000")
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    override = SpendLimit(
        organization_id=org,
        limit_key="llm.input_token",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal(7),
        hard_stop=True,
    )
    db_session.add(override)
    db_session.flush()

    # Layer one.
    limits = spend.effective_limits(
        db_session, organization_id=org, limit_key="llm.input_token", lock=False
    )
    assert [limit.max_quantity for limit in limits] == [Decimal(7)]
    assert limits[0].source == "ORGANIZATION"

    # Layer two.
    override.is_active = False
    db_session.flush()
    limits = spend.effective_limits(
        db_session, organization_id=org, limit_key="llm.input_token", lock=False
    )
    assert [limit.max_quantity for limit in limits] == [Decimal(1000)]
    assert limits[0].source == "TIER"
    assert limits[0].quota_tier_key == "business"
    assert limits[0].quota_tier_version == tier.version

    # Layer three.
    organization = db_session.get(Organization, org)
    organization.quota_tier_id = None
    db_session.flush()
    quota_service.clear_cache()

    limits = spend.effective_limits(
        db_session, organization_id=org, limit_key="llm.input_token", lock=False
    )
    assert all(limit.source == "PLATFORM_DEFAULT" for limit in limits)
    assert all(limit.is_default for limit in limits)


def test_no_tier_is_not_unlimited(db_session: Session, org: uuid.UUID):
    limits = spend.effective_limits(
        db_session, organization_id=org, limit_key="llm.input_token", lock=False
    )
    assert limits, "an unassigned organization must still inherit a ceiling"


def test_tier_versions_do_not_move_history(db_session: Session, org: uuid.UUID, book):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    _publish_tier(db_session, version=1, max_quantity="1000", effective_from=old)
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    _publish_tier(
        db_session,
        version=2,
        max_quantity="9999",
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
    )

    back_then = quota_service.tier_limits(
        db_session,
        organization_id=org,
        limit_key="llm.input_token",
        at=old + timedelta(days=1),
    )
    now = quota_service.tier_limits(
        db_session, organization_id=org, limit_key="llm.input_token"
    )

    assert [entry.max_quantity for entry in back_then] == [Decimal(1000)]
    assert [entry.quota_tier_version for entry in back_then] == [1]
    assert [entry.max_quantity for entry in now] == [Decimal(9999)]
    assert [entry.quota_tier_version for entry in now] == [2]


# ---------------------------------------------------------------------------
# The gate's second half — policy
# ---------------------------------------------------------------------------


def test_refuse_produces_402_not_429(db_session: Session, org: uuid.UUID, book):
    from app.core.exception_handlers import _EXCEPTION_MAPPING

    _publish_tier(
        db_session, max_quantity="10", policy=OveragePolicy.REFUSE.value
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db_session,
            organization_id=org,
            event_type="llm.input_token",
            quantity=50,
            cost_micros=10,
        )

    status_code, code = _EXCEPTION_MAPPING[SpendLimitExceededError]
    rate_limit_status, _ = _EXCEPTION_MAPPING[RateLimitExceededError]
    assert status_code == 402
    assert code == "SPEND_LIMIT_EXCEEDED"
    assert rate_limit_status == 429
    assert status_code != rate_limit_status


def test_allow_and_warn_permits_and_audits(
    db_session: Session, org: uuid.UUID, book
):
    _publish_tier(
        db_session, max_quantity="10", policy=OveragePolicy.ALLOW_AND_WARN.value
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    spend.ensure_within_limits(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=50,
        cost_micros=10,
    )


def test_grace_absorbs_estimate_drift(db_session: Session, org: uuid.UUID, book):
    _publish_tier(
        db_session,
        max_quantity="100",
        policy=OveragePolicy.REFUSE.value,
        grace="10",
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    spend.ensure_within_limits(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=105,
        cost_micros=21,
    )

    with pytest.raises(SpendLimitExceededError):
        spend.ensure_within_limits(
            db_session,
            organization_id=org,
            event_type="llm.input_token",
            quantity=120,
            cost_micros=24,
        )


def test_allow_and_bill_writes_an_overage_row_at_the_overage_price(
    db_session: Session, org: uuid.UUID, book
):
    _publish_tier(
        db_session,
        max_quantity="1000",
        policy=OveragePolicy.ALLOW_AND_BILL.value,
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    now = datetime.now(timezone.utc)
    emit(db_session, org=org, occurred_at=now - timedelta(minutes=10), quantity=900, cost=180)
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.flush()

    emit(db_session, org=org, occurred_at=now - timedelta(minutes=1), quantity=300, cost=60)
    db_session.flush()

    outcome = quota_service.bill_overage_if_any(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=300,
        provider="groq",
        model="llama-3.3-70b",
        idempotency_key="test:settle",
    )
    db_session.flush()

    assert outcome.billed is True
    assert outcome.overage_quantity == Decimal(200)
    assert outcome.cost_micros == 100

    row = db_session.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == org,
            UsageEvent.event_type == "llm.input_token.overage",
        )
    ).scalar_one()

    assert row.quantity == Decimal(200)
    assert row.cost_micros == 100
    assert Decimal(str(row.unit_price_micros)) == OVERAGE_MICROS
    assert row.price_book_id is not None
    assert row.details["overage_of"] == "llm.input_token"
    assert row.details["quota_tier"] == "business"
    assert row.details["ceiling_quantity"] == "1000"


def test_overage_row_does_not_count_against_its_own_ceiling(
    db_session: Session, org: uuid.UUID, book
):
    _publish_tier(
        db_session,
        max_quantity="100",
        policy=OveragePolicy.ALLOW_AND_BILL.value,
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    now = datetime.now(timezone.utc)
    emit(db_session, org=org, occurred_at=now - timedelta(minutes=1), quantity=150, cost=30)
    db_session.flush()
    quota_service.bill_overage_if_any(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=150,
        provider="groq",
        idempotency_key="test:one",
    )
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.flush()

    from app.services import usage_service

    totals = usage_service.usage_totals_bounded(
        db_session,
        organization_id=org,
        since=rollup_service.month_bucket(now),
    )
    assert totals["llm.input_token"][0] == Decimal(150)
    assert totals["llm.input_token.overage"][0] == Decimal(50)


def test_overage_is_not_billed_twice_for_the_same_row(
    db_session: Session, org: uuid.UUID, book
):
    _publish_tier(
        db_session,
        max_quantity="100",
        policy=OveragePolicy.ALLOW_AND_BILL.value,
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.flush()

    now = datetime.now(timezone.utc)
    emit(db_session, org=org, occurred_at=now, quantity=150, cost=30)
    db_session.flush()

    for _ in range(2):
        try:
            quota_service.bill_overage_if_any(
                db_session,
                organization_id=org,
                event_type="llm.input_token",
                quantity=150,
                provider="groq",
                idempotency_key="test:same-key",
            )
            db_session.flush()
        except Exception:
            db_session.rollback()

    count = db_session.execute(
        text(
            "SELECT count(*) FROM usage_events WHERE organization_id = :o "
            "AND event_type = 'llm.input_token.overage'"
        ),
        {"o": org},
    ).scalar_one()
    assert count == 1


def test_explicit_override_governs_and_bills_no_overage(
    db_session: Session, org: uuid.UUID, book
):
    _publish_tier(
        db_session,
        max_quantity="100",
        policy=OveragePolicy.ALLOW_AND_BILL.value,
    )
    quota_service.assign_tier(db_session, organization_id=org, tier_key="business")
    db_session.add(
        SpendLimit(
            organization_id=org,
            limit_key="llm.input_token",
            period=SpendLimitPeriod.MONTH,
            max_quantity=Decimal(50),
            hard_stop=True,
        )
    )
    db_session.flush()

    now = datetime.now(timezone.utc)
    emit(db_session, org=org, occurred_at=now, quantity=150, cost=30)
    db_session.flush()

    outcome = quota_service.bill_overage_if_any(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=150,
        provider="groq",
        idempotency_key="test:override",
    )
    assert outcome.billed is False
    assert outcome.reason == "explicit_override_governs"


def test_overage_of_an_overage_is_refused(db_session: Session, org: uuid.UUID, book):
    outcome = quota_service.bill_overage_if_any(
        db_session,
        organization_id=org,
        event_type="llm.input_token.overage",
        quantity=10,
    )
    assert outcome.billed is False
    assert outcome.reason == "already_an_overage_row"


# ---------------------------------------------------------------------------
# Publication invariants
# ---------------------------------------------------------------------------


def test_allow_and_bill_without_a_resolvable_price_is_refused_at_publish(
    db_session: Session
):
    with pytest.raises(quota_service.QuotaTierValidationError) as exc:
        quota_service.publish_tier(
            db_session,
            key="business",
            display_name="Business",
            version=99,
            effective_from=datetime.now(timezone.utc),
            entries=[
                TierEntrySpec(
                    limit_key="llm.input_token",
                    max_quantity=Decimal(100),
                    overage_policy=OveragePolicy.ALLOW_AND_BILL.value,
                    overage_price_tier_key="a-tier-key-no-book-carries",
                )
            ],
        )
    assert "price book" in str(exc.value).lower()
    db_session.rollback()


def test_allow_and_bill_without_a_tier_key_is_refused(db_session: Session):
    with pytest.raises(quota_service.QuotaTierValidationError):
        quota_service.publish_tier(
            db_session,
            key="business",
            display_name="Business",
            version=98,
            effective_from=datetime.now(timezone.utc),
            entries=[
                TierEntrySpec(
                    limit_key="llm.input_token",
                    max_quantity=Decimal(100),
                    overage_policy=OveragePolicy.ALLOW_AND_BILL.value,
                )
            ],
        )
    db_session.rollback()


def test_entry_with_no_ceiling_is_refused(db_session: Session):
    with pytest.raises(quota_service.QuotaTierValidationError):
        quota_service.publish_tier(
            db_session,
            key="free",
            display_name="Free",
            version=97,
            effective_from=datetime.now(timezone.utc),
            entries=[TierEntrySpec(limit_key="llm.input_token")],
        )
    db_session.rollback()


def test_unknown_limit_key_is_refused(db_session: Session):
    with pytest.raises(quota_service.QuotaTierValidationError):
        quota_service.publish_tier(
            db_session,
            key="free",
            display_name="Free",
            version=96,
            effective_from=datetime.now(timezone.utc),
            entries=[
                TierEntrySpec(limit_key="llm.telepathy", max_quantity=Decimal(1))
            ],
        )
    db_session.rollback()


def test_published_tier_is_immutable(db_session: Session, book):
    tier = _publish_tier(db_session, version=5)
    with _expect_refusal():
        db_session.execute(
            text("UPDATE quota_tiers SET display_name = 'X' WHERE id = :i"),
            {"i": str(tier.id)},
        )
        db_session.flush()
    db_session.rollback()


def test_published_tier_cannot_gain_entries(db_session: Session, book):
    tier = _publish_tier(db_session, version=6)
    with _expect_refusal():
        db_session.add(
            QuotaTierEntry(
                quota_tier_id=tier.id,
                limit_key="ocr.page",
                period=SpendLimitPeriod.MONTH,
                max_quantity=Decimal(1),
                overage_policy=OveragePolicy.REFUSE.value,
            )
        )
        db_session.flush()
    db_session.rollback()


def test_publishing_closes_the_predecessor(db_session: Session, book):
    first = _publish_tier(
        db_session,
        version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
    )
    changeover = datetime.now(timezone.utc) + timedelta(days=1)
    _publish_tier(db_session, version=2, effective_from=changeover)
    db_session.refresh(first)
    assert first.effective_to == changeover


# ---------------------------------------------------------------------------
# The derived vocabulary
# ---------------------------------------------------------------------------


def test_every_billable_type_has_an_overage_twin():
    for name, descriptor in list(USAGE_EVENT_TYPES.items()):
        if is_overage_type(name) or not descriptor.billable:
            continue
        twin = overage_type_for(name)
        assert twin in USAGE_EVENT_TYPES
        assert USAGE_EVENT_TYPES[twin].unit == descriptor.unit
        assert len(twin) <= 64


def test_non_billable_types_have_no_overage_twin():
    with pytest.raises(ValueError):
        overage_type_for("embedding.backfill_token")


def test_wildcard_is_not_an_overage_key():
    with pytest.raises(ValueError):
        overage_type_for(TOTAL_COST_KEY)