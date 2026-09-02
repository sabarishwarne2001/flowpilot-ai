"""ARCH-17 — target resolution, histogram maths, breach detection, sealing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core import slo_recorder
from app.core.request_context import StageRecord, request_scope, stage
from app.models.slo import (
    DEFAULT_LATENCY_BOUNDS_MS,
    SLODefinition,
    SLOMeasurement,
    SLOMethod,
    SLOObservation,
    SLOUnit,
    SLOWindow,
    bucket_bounds_for,
)
from app.services import slo_service

pytestmark = pytest.mark.usefixtures("test_database")

BOUNDS = list(DEFAULT_LATENCY_BOUNDS_MS)


def _bucketise(samples: list[float]) -> list[int]:
    from bisect import bisect_left

    counts = [0] * (len(BOUNDS) + 1)
    for value in samples:
        counts[bisect_left(BOUNDS, value)] += 1
    return counts


def _observe(
    db: Session,
    *,
    organization_id,
    slo_key: str,
    samples: list[float],
    hour: datetime,
    error_count: int = 0,
) -> SLOObservation:
    row = SLOObservation(
        organization_id=organization_id,
        slo_key=slo_key,
        window_start=hour.replace(minute=0, second=0, microsecond=0),
        sample_count=len(samples),
        error_count=error_count,
        sum_value=Decimal(str(sum(samples))),
        bucket_bounds=BOUNDS,
        bucket_counts=_bucketise(samples),
    )
    db.add(row)
    db.flush()
    return row


def _today(hour: int = 6) -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=min(hour, now.hour), minute=0, second=0, microsecond=0)


def test_registry_default_applies_when_nothing_is_seeded(db_session, tenant):
    resolved = slo_service.resolve_slo_targets(db_session, tenant.organization.id)

    assert resolved, "an unconfigured organization still has platform targets"
    assert all(item.source == "REGISTRY_DEFAULT" for item in resolved)

    retrieval = next(r for r in resolved if r.slo_key == "rag.retrieval.p95_ms")
    assert retrieval.target_value == Decimal("300.0")
    assert retrieval.unit is SLOUnit.MILLISECONDS


def test_platform_row_overrides_registry(db_session, tenant):
    slo_service.seed_platform_defaults(db_session)
    db_session.commit()

    resolved = slo_service.resolve_slo_targets(db_session, tenant.organization.id)
    assert all(item.source == "PLATFORM_DEFAULT" for item in resolved)


def test_tenant_row_overrides_platform(db_session, tenant):
    slo_service.seed_platform_defaults(db_session)
    slo_service.set_target(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        target_value=Decimal("150"),
        is_contractual=True,
    )
    db_session.commit()

    resolved = slo_service.resolve_slo_targets(db_session, tenant.organization.id)
    retrieval = next(r for r in resolved if r.slo_key == "rag.retrieval.p95_ms")

    assert retrieval.source == "ORGANIZATION"
    assert retrieval.target_value == Decimal("150")
    assert retrieval.is_contractual is True

    rerank = next(r for r in resolved if r.slo_key == "rag.rerank.p95_ms")
    assert rerank.source == "PLATFORM_DEFAULT"


def test_one_tenants_override_is_invisible_to_another(db_session, tenant):
    slo_service.set_target(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        target_value=Decimal("42"),
    )
    db_session.commit()

    other = slo_service.resolve_slo_targets(
        db_session, tenant.foreign_workspace.organization_id
    )
    retrieval = next(r for r in other if r.slo_key == "rag.retrieval.p95_ms")
    assert retrieval.target_value == Decimal("300.0")
    assert retrieval.source == "REGISTRY_DEFAULT"


def test_ratio_target_above_one_is_refused(db_session, tenant):
    with pytest.raises(slo_service.SLOServiceError):
        slo_service.set_target(
            db_session,
            organization_id=tenant.organization.id,
            slo_key="api.availability",
            target_value=Decimal("99.9"),
        )


def test_unknown_slo_key_is_refused(db_session, tenant):
    from app.core.slo_registry import SLORegistryError

    with pytest.raises(SLORegistryError):
        slo_service.set_target(
            db_session,
            organization_id=tenant.organization.id,
            slo_key="rag.retrieval.p99_ms",
            target_value=Decimal("100"),
        )


def test_target_is_always_a_bucket_boundary():
    bounds = bucket_bounds_for(Decimal("137"), unit=SLOUnit.MILLISECONDS)
    assert 137.0 in bounds
    assert list(bounds) == sorted(bounds)


def test_percentile_is_interpolated_within_its_bucket(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 950 + [900.0] * 50,
        hour=hour,
    )
    db_session.commit()

    histogram = slo_service.load_histogram(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        window_start=slo_service.window_start_for(SLOWindow.DAY, hour),
        window_end=slo_service.window_end_for(
            SLOWindow.DAY, slo_service.window_start_for(SLOWindow.DAY, hour)
        ),
    )

    assert histogram.sample_count == 1000
    assert histogram.percentile(0.95) <= Decimal("60")
    assert histogram.count_at_or_below(300.0) == 950


def test_count_at_or_below_is_none_off_boundary(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 10,
        hour=hour,
    )
    db_session.commit()

    start = slo_service.window_start_for(SLOWindow.DAY, hour)
    histogram = slo_service.load_histogram(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        window_start=start,
        window_end=slo_service.window_end_for(SLOWindow.DAY, start),
    )
    assert histogram.count_at_or_below(137.0) is None
    assert histogram.count_at_or_below(300.0) == 10


def test_mixed_bucket_schedules_are_not_added_together(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 100,
        hour=hour,
    )
    stale = SLOObservation(
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        window_start=(hour - timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        ),
        sample_count=5,
        error_count=0,
        sum_value=Decimal("50"),
        bucket_bounds=[10.0, 100.0],
        bucket_counts=[5, 0, 0],
    )
    db_session.add(stale)
    db_session.commit()

    start = slo_service.window_start_for(SLOWindow.DAY, hour)
    histogram = slo_service.load_histogram(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        window_start=start,
        window_end=slo_service.window_end_for(SLOWindow.DAY, start),
    )
    assert histogram.sample_count == 100
    assert list(histogram.bounds) == BOUNDS


def test_compliant_window_does_not_breach(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 950 + [900.0] * 50,
        hour=hour,
    )
    db_session.commit()

    measurement = slo_service.record_measurement(
        db_session, organization_id=tenant.organization.id, slo_key="rag.retrieval.p95_ms"
    )
    db_session.commit()

    assert measurement.breached is False
    assert measurement.sample_count == 1000
    assert measurement.method is SLOMethod.HISTOGRAM_INTERPOLATED
    assert measurement.details["target_is_bucket_boundary"] is True


def test_breach_is_decided_exactly_at_the_boundary(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 900 + [900.0] * 100,
        hour=hour,
    )
    db_session.commit()

    measurement = slo_service.record_measurement(
        db_session, organization_id=tenant.organization.id, slo_key="rag.retrieval.p95_ms"
    )
    db_session.commit()

    assert measurement.breached is True
    assert measurement.details["samples_at_or_below_target"] == 900
    assert measurement.details["samples_required"] == 950.0
    assert "breach_from_interpolation" not in measurement.details


def test_contractual_breach_is_suppressed_on_a_tiny_sample(db_session, tenant):
    slo_service.set_target(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        target_value=Decimal("300"),
        is_contractual=True,
    )
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[5000.0] * 11,
        hour=hour,
    )
    db_session.commit()

    measurement = slo_service.record_measurement(
        db_session, organization_id=tenant.organization.id, slo_key="rag.retrieval.p95_ms"
    )
    db_session.commit()

    assert measurement.is_contractual is True
    assert measurement.breached is False
    assert measurement.details["suppressed_low_sample_breach"] is True
    assert measurement.observed_value > Decimal("300")


def test_empty_window_never_breaches(db_session, tenant):
    measurement = slo_service.record_measurement(
        db_session, organization_id=tenant.organization.id, slo_key="rag.retrieval.p95_ms"
    )
    db_session.commit()

    assert measurement.sample_count == 0
    assert measurement.breached is False


def test_ratio_measurement_is_exact(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="api.availability",
        samples=[1.0] * 990 + [0.0] * 10,
        hour=hour,
        error_count=10,
    )
    db_session.commit()

    measurement = slo_service.record_measurement(
        db_session, organization_id=tenant.organization.id, slo_key="api.availability"
    )
    db_session.commit()

    assert measurement.method is SLOMethod.EXACT
    assert measurement.observed_value == Decimal("0.9900")
    assert measurement.breached is True


def test_sealed_window_cannot_be_rewritten(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 200,
        hour=hour,
    )
    db_session.commit()

    sealed = slo_service.record_measurement(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        seal=True,
    )
    db_session.commit()
    assert sealed.sealed_at is not None

    with pytest.raises(slo_service.MeasurementSealedError):
        slo_service.record_measurement(
            db_session,
            organization_id=tenant.organization.id,
            slo_key="rag.retrieval.p95_ms",
        )


def test_target_change_does_not_rewrite_a_sealed_verdict(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 900 + [900.0] * 100,
        hour=hour,
    )
    db_session.commit()

    sealed = slo_service.record_measurement(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        seal=True,
    )
    db_session.commit()
    assert sealed.breached is True
    assert sealed.target_value == Decimal("300.0000")

    slo_service.set_target(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        target_value=Decimal("5000"),
    )
    db_session.commit()
    db_session.refresh(sealed)

    assert sealed.target_value == Decimal("300.0000")
    assert sealed.breached is True


def test_summary_covers_every_registry_key(db_session, tenant):
    from app.core.slo_registry import SLO_REGISTRY

    summary = slo_service.get_tenant_slo_summary(db_session, tenant.organization.id)
    assert {entry.effective.slo_key for entry in summary.entries} == set(SLO_REGISTRY)


def test_summary_excludes_other_tenants_observations(db_session, tenant):
    hour = _today()
    _observe(
        db_session,
        organization_id=tenant.foreign_workspace.organization_id,
        slo_key="rag.retrieval.p95_ms",
        samples=[9999.0] * 500,
        hour=hour,
    )
    db_session.commit()

    summary = slo_service.get_tenant_slo_summary(db_session, tenant.organization.id)
    retrieval = next(
        e for e in summary.entries if e.effective.slo_key == "rag.retrieval.p95_ms"
    )
    assert retrieval.sample_count == 0
    assert retrieval.observed_value is None


def test_stage_durations_reach_the_recorder():
    slo_recorder.recorder.drain()
    slo_recorder.install()
    organization_id = str(uuid.uuid4())
    try:
        with request_scope(organization_id=organization_id):
            with stage("retrieval"):
                pass
        drained = slo_recorder.recorder.drain()
    finally:
        slo_recorder.uninstall()

    keys = {(key.organization_id, key.slo_key) for key, _ in drained}
    assert (organization_id, "rag.retrieval.p95_ms") in keys


def test_a_stage_with_no_organization_is_dropped():
    slo_recorder.recorder.drain()
    slo_recorder.install()
    try:
        with request_scope():
            with stage("retrieval"):
                pass
        assert slo_recorder.recorder.drain() == []
    finally:
        slo_recorder.uninstall()


def test_a_failed_stage_is_recorded_as_an_error_not_omitted():
    slo_recorder.recorder.drain()
    slo_recorder.install()
    organization_id = str(uuid.uuid4())
    try:
        with request_scope(organization_id=organization_id):
            with pytest.raises(RuntimeError):
                with stage("rerank"):
                    raise RuntimeError("reranker unreachable")
        drained = dict(
            ((key.organization_id, key.slo_key), series) for key, series in slo_recorder.recorder.drain()
        )
    finally:
        slo_recorder.uninstall()

    series = drained[(organization_id, "rag.rerank.p95_ms")]
    assert series.sample_count == 1
    assert series.error_count == 1


def test_recorder_never_raises_into_the_request(monkeypatch):
    def exploding_sink(record: StageRecord) -> None:
        raise ValueError("sink is broken")

    from app.core import request_context

    request_context.set_stage_sink(exploding_sink)
    try:
        with request_scope(organization_id=str(uuid.uuid4())):
            with stage("retrieval"):
                pass
    finally:
        request_context.set_stage_sink(None)
