"""ARCH-21 — tier enforcement, usage rollups and HNSW ef_search tuning.

WHAT THESE TESTS ARE CAREFUL ABOUT
==================================

`conftest.disable_rate_limiting_in_tests` is autouse and sets
`ENVIRONMENT="test"` and `RATE_LIMIT_ENABLED=False`. `consume_rate_limit()`
returns `allowed=True` unconditionally under either. Any test that asserted a
429 through the TestClient would therefore pass without the limiter having
made a decision at all — a green test proving nothing.

So the tier-enforcement tests below drive the backend directly
(`InMemoryBackend.consume`) and construct the policy the way `require_api_key`
does. That tests the arithmetic that actually refuses a request, rather than
the harness's bypass of it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.api_tiers import (
    API_RATE_TIER_VALUES,
    EF_SEARCH_CEILING,
    EF_SEARCH_FLOOR,
    TIER_PROFILES,
    ApiRateTier,
    ApiTierError,
    assignable_tiers,
    ceiling_for_quota_tier,
    clamp_ef_search,
    ef_search_for,
    is_within_ceiling,
    parse_tier,
    profile_for,
)
from app.core.rate_limit.backend import InMemoryBackend
from app.core.scopes import PUBLIC_API_SCOPES, ApiKeyScope
from app.core.usage_events import USAGE_EVENT_TYPES, is_limit_key, resolve
from app.models.api_key import ApiKey
from app.models.organization import OrganizationRole
from app.models.public_api import (
    ApiKeyUsageDaily,
    LATENCY_BOUNDS_MS,
    LATENCY_BUCKET_COUNT,
    bucket_index_for,
    empty_buckets,
)
from app.models.usage_event import UsageEvent
from app.services import developer_portal_service, public_api_service


# ===========================================================================
# Helpers
# ===========================================================================


def _issue_key(
    db,
    tenant,
    *,
    scopes: list[str] | None = None,
    tier: ApiRateTier = ApiRateTier.FREE,
    enabled: bool = True,
) -> ApiKey:
    from app.core.api_key_secret import generate_secret, hash_secret

    key = ApiKey(
        organization_id=tenant.organization.id,
        user_id=tenant.org_admin.user.id,
        name=f"gateway-{uuid.uuid4().hex[:8]}",
        secret_hash=hash_secret(generate_secret()),
        scopes=scopes or [ApiKeyScope.PUBLIC_DOCUMENTS_READ.value],
        tier_key=tier.value,
        rate_limit_per_minute=profile_for(tier).rate_limit_per_minute,
        monthly_request_quota=profile_for(tier).monthly_request_quota,
        is_public_api_enabled=enabled,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


# ===========================================================================
# Tier vocabulary
# ===========================================================================


@pytest.mark.no_db
def test_tier_vocabulary_is_the_four_named_tiers() -> None:
    assert API_RATE_TIER_VALUES == ("FREE", "BUILDER", "PRO", "ENTERPRISE")
    assert set(TIER_PROFILES) == set(ApiRateTier)


@pytest.mark.no_db
def test_tier_ranks_are_contiguous_and_limits_monotonic() -> None:
    ordered = sorted(TIER_PROFILES.values(), key=lambda p: p.rank)
    assert [p.rank for p in ordered] == list(range(len(ordered)))

    for attribute in (
        "rate_limit_per_minute",
        "monthly_request_quota",
        "ef_search",
    ):
        values = [getattr(p, attribute) for p in ordered]
        assert values == sorted(values), f"{attribute} is not monotonic"
        assert len(set(values)) == len(values), f"{attribute} has a duplicate"


@pytest.mark.no_db
def test_parse_tier_refuses_an_unknown_value() -> None:
    with pytest.raises(ApiTierError):
        parse_tier("PLATINUM")
    assert parse_tier("pro") is ApiRateTier.PRO
    assert parse_tier(ApiRateTier.FREE) is ApiRateTier.FREE


# ===========================================================================
# The quota-tier ceiling (decision D3)
# ===========================================================================


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("quota_key", "expected"),
    [
        ("free", ApiRateTier.FREE),
        ("developer", ApiRateTier.BUILDER),
        ("business", ApiRateTier.PRO),
        ("enterprise", ApiRateTier.ENTERPRISE),
        ("ENTERPRISE", ApiRateTier.ENTERPRISE),
    ],
)
def test_quota_tier_maps_to_a_rate_tier_ceiling(quota_key, expected) -> None:
    assert ceiling_for_quota_tier(quota_key) is expected


@pytest.mark.no_db
def test_no_quota_tier_in_force_falls_back_to_free_not_enterprise() -> None:
    assert ceiling_for_quota_tier(None) is ApiRateTier.FREE
    assert ceiling_for_quota_tier("") is ApiRateTier.FREE
    assert ceiling_for_quota_tier("something-invented") is ApiRateTier.FREE


@pytest.mark.no_db
def test_assignable_tiers_stop_at_the_ceiling() -> None:
    assert [t.key for t in assignable_tiers(ApiRateTier.BUILDER)] == [
        ApiRateTier.FREE,
        ApiRateTier.BUILDER,
    ]
    assert is_within_ceiling(ApiRateTier.FREE, ApiRateTier.PRO)
    assert not is_within_ceiling(ApiRateTier.ENTERPRISE, ApiRateTier.PRO)


def test_assign_tier_refuses_above_the_plan_ceiling(db_session, tenant) -> None:
    from app.models.organization import OrganizationMember

    key = _issue_key(db_session, tenant, tier=ApiRateTier.FREE)
    actor = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == tenant.org_admin.user.id
        )
    ).scalar_one()

    with pytest.raises(developer_portal_service.TierCeilingExceededError):
        developer_portal_service.assign_tier(
            db_session,
            key=key,
            tier=ApiRateTier.ENTERPRISE,
            actor=actor,
        )

    db_session.rollback()
    db_session.refresh(key)
    assert key.tier_key == ApiRateTier.FREE.value


def test_assign_tier_within_the_ceiling_denormalises_the_profile(
    db_session, tenant
) -> None:
    from app.models.organization import OrganizationMember

    key = _issue_key(db_session, tenant, tier=ApiRateTier.FREE)
    actor = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == tenant.org_admin.user.id
        )
    ).scalar_one()

    developer_portal_service.assign_tier(
        db_session, key=key, tier=ApiRateTier.FREE, actor=actor
    )
    db_session.commit()
    db_session.refresh(key)

    profile = profile_for(ApiRateTier.FREE)
    assert key.rate_limit_per_minute == profile.rate_limit_per_minute
    assert key.monthly_request_quota == profile.monthly_request_quota


def test_a_member_role_actor_cannot_assign_a_tier(db_session, tenant) -> None:
    from app.core.exceptions import OrganizationPermissionDeniedError
    from app.models.organization import OrganizationMember

    key = _issue_key(db_session, tenant)
    member = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == tenant.contributor.user.id
        )
    ).scalar_one()
    assert member.role is OrganizationRole.MEMBER

    with pytest.raises(OrganizationPermissionDeniedError):
        developer_portal_service.assign_tier(
            db_session, key=key, tier=ApiRateTier.FREE, actor=member
        )


# ===========================================================================
# Scope vocabulary and the D1 repair
# ===========================================================================


def test_billing_read_is_grantable_at_the_database(db_session, tenant) -> None:
    key = _issue_key(
        db_session,
        tenant,
        scopes=[
            ApiKeyScope.BILLING_READ.value,
            ApiKeyScope.PUBLIC_DOCUMENTS_READ.value,
        ],
    )
    assert ApiKeyScope.BILLING_READ.value in key.scopes


def test_every_declared_scope_is_storable(db_session, tenant) -> None:
    for scope in ApiKeyScope:
        key = _issue_key(
            db_session,
            tenant,
            scopes=[scope.value, ApiKeyScope.PUBLIC_DOCUMENTS_READ.value],
        )
        assert scope.value in key.scopes


def test_an_invented_scope_is_still_refused(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _issue_key(db_session, tenant, scopes=["totally:invented"])
    db_session.rollback()


def test_gateway_enabled_requires_a_public_scope(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _issue_key(
            db_session,
            tenant,
            scopes=[ApiKeyScope.ORGANIZATIONS_READ.value],
            enabled=True,
        )
    db_session.rollback()


def test_public_scopes_are_single_colon() -> None:
    import re

    pattern = re.compile(r"^[a-z_]+:[a-z_*]+$")
    for scope in PUBLIC_API_SCOPES:
        assert pattern.fullmatch(scope.value), (
            f"{scope.value} would be truncated by verify_scope_vocabulary.py "
            "S.2's single-colon extraction pattern"
        )


# ===========================================================================
# api.request metering (decision D2)
# ===========================================================================


@pytest.mark.no_db
def test_api_request_is_registered_and_not_billable() -> None:
    descriptor = resolve("api.request")
    assert descriptor.billable is False
    assert descriptor.unit.value == "request"
    assert "api.request.overage" not in USAGE_EVENT_TYPES
    assert is_limit_key("api.request") is False


def test_meter_request_writes_the_ledger_and_the_rollup(
    db_session, tenant
) -> None:
    key = _issue_key(db_session, tenant)

    public_api_service.meter_request(
        db_session,
        key=key,
        route="/api/v1/public/documents",
        method="GET",
        status_code=200,
        latency_ms=42.0,
        workspace_id=tenant.workspace.id,
    )
    db_session.commit()

    event = db_session.execute(
        select(UsageEvent).where(UsageEvent.event_type == "api.request")
    ).scalar_one()
    assert event.api_key_id == key.id
    assert event.actor_id is None
    assert event.organization_id == tenant.organization.id

    rollup = db_session.execute(
        select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
    ).scalar_one()
    assert rollup.request_count == 1
    assert rollup.error_count == 0
    assert rollup.total_latency_ms == 42


def test_repeated_requests_accumulate_on_one_row(db_session, tenant) -> None:
    key = _issue_key(db_session, tenant)

    for latency in (10.0, 20.0, 30.0):
        public_api_service.record_daily_usage(
            db_session,
            api_key_id=key.id,
            organization_id=tenant.organization.id,
            latency_ms=latency,
            is_error=False,
            is_throttled=False,
        )
        db_session.commit()

    rows = (
        db_session.execute(
            select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].request_count == 3
    assert rows[0].total_latency_ms == 60


def test_a_throttled_request_carries_no_latency(db_session, tenant) -> None:
    key = _issue_key(db_session, tenant)

    public_api_service.record_daily_usage(
        db_session,
        api_key_id=key.id,
        organization_id=tenant.organization.id,
        latency_ms=2.0,
        is_error=True,
        is_throttled=True,
    )
    db_session.commit()

    rollup = db_session.execute(
        select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
    ).scalar_one()
    assert rollup.request_count == 1
    assert rollup.error_count == 1
    assert rollup.throttled_count == 1
    assert rollup.total_latency_ms == 0
    assert sum(rollup.latency_bucket_counts) == 0
    assert rollup.mean_latency_ms is None


def test_the_histogram_lands_in_the_right_bucket(db_session, tenant) -> None:
    key = _issue_key(db_session, tenant)
    latency = 240.0
    expected_index = bucket_index_for(latency)

    public_api_service.record_daily_usage(
        db_session,
        api_key_id=key.id,
        organization_id=tenant.organization.id,
        latency_ms=latency,
        is_error=False,
        is_throttled=False,
    )
    db_session.commit()

    rollup = db_session.execute(
        select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
    ).scalar_one()
    buckets = list(rollup.latency_bucket_counts)
    assert len(buckets) == LATENCY_BUCKET_COUNT
    assert buckets[expected_index] == 1
    assert sum(buckets) == 1


@pytest.mark.no_db
def test_bucket_index_saturates_at_the_top_bound() -> None:
    assert bucket_index_for(1.0) == 0
    assert bucket_index_for(LATENCY_BOUNDS_MS[-1]) == LATENCY_BUCKET_COUNT - 1
    assert bucket_index_for(10_000_000.0) == LATENCY_BUCKET_COUNT - 1
    assert len(empty_buckets()) == LATENCY_BUCKET_COUNT


# ===========================================================================
# Percentiles: estimated, labelled, and never zero (invariant)
# ===========================================================================


def test_percentiles_are_none_for_a_window_with_no_traffic(
    db_session, tenant
) -> None:
    key = _issue_key(db_session, tenant)
    metrics = developer_portal_service.key_metrics(
        db_session,
        organization_id=tenant.organization.id,
        api_key_id=key.id,
        days=7,
    )
    assert metrics["total_requests"] == 0
    assert metrics["p50_latency_ms"] is None
    assert metrics["p95_latency_ms"] is None
    assert metrics["mean_latency_ms"] is None
    assert metrics["error_rate"] is None
    assert metrics["latency_method"] == "HISTOGRAM_INTERPOLATED"
    assert len(metrics["series"]) == 7


def test_percentiles_are_interpolated_from_the_histogram(
    db_session, tenant
) -> None:
    key = _issue_key(db_session, tenant)

    for latency in [15.0] * 90 + [4000.0] * 10:
        public_api_service.record_daily_usage(
            db_session,
            api_key_id=key.id,
            organization_id=tenant.organization.id,
            latency_ms=latency,
            is_error=False,
            is_throttled=False,
        )
        db_session.commit()

    metrics = developer_portal_service.key_metrics(
        db_session,
        organization_id=tenant.organization.id,
        api_key_id=key.id,
        days=7,
    )
    assert metrics["total_requests"] == 100
    assert metrics["p50_latency_ms"] is not None
    assert metrics["p95_latency_ms"] is not None
    assert metrics["p50_latency_ms"] < 100
    assert metrics["p95_latency_ms"] > 1000


def test_error_rate_is_none_rather_than_zero_over_no_requests(
    db_session, tenant
) -> None:
    key = _issue_key(db_session, tenant)
    metrics = developer_portal_service.key_metrics(
        db_session,
        organization_id=tenant.organization.id,
        api_key_id=key.id,
    )
    assert metrics["error_rate"] is None


# ===========================================================================
# HNSW ef_search tuning (decision D6)
# ===========================================================================


@pytest.mark.no_db
def test_ef_search_increases_with_tier_and_is_clamped() -> None:
    ordered = sorted(TIER_PROFILES.values(), key=lambda p: p.rank)
    values = [ef_search_for(p.key) for p in ordered]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
    assert all(EF_SEARCH_FLOOR <= v <= EF_SEARCH_CEILING for v in values)


@pytest.mark.no_db
def test_clamp_refuses_to_pass_an_unbounded_value_through() -> None:
    assert clamp_ef_search(10_000_000) == EF_SEARCH_CEILING
    assert clamp_ef_search(1) == EF_SEARCH_FLOOR
    assert clamp_ef_search(-5) == EF_SEARCH_FLOOR


def test_ensure_iterative_scan_applies_the_tier_value(db_session) -> None:
    from app.core.config import settings
    from app.db.chunk_scope import ensure_iterative_scan

    if not settings.APPLY_HNSW_SESSION_DEFAULTS:
        pytest.skip("pgvector session defaults are disabled in this environment")

    target = ef_search_for(ApiRateTier.ENTERPRISE)

    applied = ensure_iterative_scan(db_session, ef_search=target)
    assert applied == target

    observed = db_session.execute(text("SHOW hnsw.ef_search")).scalar_one()
    assert int(observed) == target
    db_session.rollback()


def test_ensure_iterative_scan_defaults_to_the_platform_setting(
    db_session,
) -> None:
    from app.core.config import settings
    from app.db.chunk_scope import ensure_iterative_scan

    if not settings.APPLY_HNSW_SESSION_DEFAULTS:
        pytest.skip("pgvector session defaults are disabled in this environment")

    applied = ensure_iterative_scan(db_session)
    assert applied == clamp_ef_search(settings.HNSW_EF_SEARCH)
    db_session.rollback()


# ===========================================================================
# Tenancy
# ===========================================================================


def test_a_foreign_workspace_is_not_found_not_forbidden(
    db_session, tenant
) -> None:
    with pytest.raises(public_api_service.WorkspaceNotFoundError) as absent:
        public_api_service.resolve_workspace(
            db_session,
            organization_id=tenant.organization.id,
            workspace_id=uuid.uuid4(),
        )

    with pytest.raises(public_api_service.WorkspaceNotFoundError) as foreign:
        public_api_service.resolve_workspace(
            db_session,
            organization_id=tenant.organization.id,
            workspace_id=tenant.foreign_workspace.id,
        )

    assert str(absent.value) == str(foreign.value)
    assert absent.value.status_code == 404


def test_list_documents_refuses_a_cross_tenant_workspace(
    db_session, tenant
) -> None:
    with pytest.raises(public_api_service.WorkspaceNotFoundError):
        public_api_service.list_documents(
            db_session,
            organization_id=tenant.organization.id,
            workspace_id=tenant.foreign_workspace.id,
        )


def test_page_size_is_capped(db_session, tenant) -> None:
    page = public_api_service.list_documents(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        page_size=100_000,
    )
    assert page.page_size == public_api_service.MAX_PAGE_SIZE


# ===========================================================================
# Rate limit arithmetic
# ===========================================================================


@pytest.mark.no_db
@pytest.mark.parametrize("tier", list(ApiRateTier))
def test_the_bucket_refuses_at_the_tier_limit(tier) -> None:
    backend = InMemoryBackend()
    limit = profile_for(tier).rate_limit_per_minute
    key = f"rl:v1:public_api_{tier.value.lower()}:kid:{uuid.uuid4()}"

    for index in range(limit):
        decision = backend.consume(key=key, limit=limit, window_seconds=60)
        assert decision.allowed, f"refused at request {index + 1} of {limit}"

    refused = backend.consume(key=key, limit=limit, window_seconds=60)
    assert not refused.allowed
    assert refused.remaining <= 0


@pytest.mark.no_db
def test_two_keys_do_not_share_a_bucket() -> None:
    backend = InMemoryBackend()
    first = f"rl:v1:public_api_free:kid:{uuid.uuid4()}"
    second = f"rl:v1:public_api_free:kid:{uuid.uuid4()}"
    limit = 3

    for _ in range(limit):
        assert backend.consume(key=first, limit=limit, window_seconds=60).allowed
    assert not backend.consume(key=first, limit=limit, window_seconds=60).allowed

    assert backend.consume(key=second, limit=limit, window_seconds=60).allowed


@pytest.mark.no_db
def test_the_gateway_policy_fails_closed() -> None:
    import inspect

    from app.api import deps

    source = inspect.getsource(deps.require_api_key)
    assert "FAIL_CLOSED" in source
    assert "rate_limit_per_minute" in source


# ===========================================================================
# Monthly quota accounting
# ===========================================================================


def test_month_to_date_excludes_last_month(db_session, tenant) -> None:
    key = _issue_key(db_session, tenant)
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    last_month = month_start - timedelta(days=1)

    for day, count in ((last_month, 500), (today, 7)):
        db_session.add(
            ApiKeyUsageDaily(
                api_key_id=key.id,
                organization_id=tenant.organization.id,
                usage_date=day,
                request_count=count,
                error_count=0,
                throttled_count=0,
                total_latency_ms=0,
                latency_bucket_counts=empty_buckets(),
            )
        )
    db_session.commit()

    assert (
        public_api_service.monthly_request_count(db_session, api_key_id=key.id)
        == 7
    )
