"""ARCH-17 — SLO endpoints: resolution, configuration, tenant isolation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.slo import DEFAULT_LATENCY_BOUNDS_MS, SLOObservation
from app.services import slo_service

pytestmark = pytest.mark.usefixtures("test_database")

BOUNDS = list(DEFAULT_LATENCY_BOUNDS_MS)


def _auth(persona) -> dict[str, str]:
    return {"Authorization": f"Bearer {persona.token}"}


def _bucketise(samples: list[float]) -> list[int]:
    from bisect import bisect_left

    counts = [0] * (len(BOUNDS) + 1)
    for value in samples:
        counts[bisect_left(BOUNDS, value)] += 1
    return counts


def _observe(db: Session, *, organization_id, slo_key, samples, error_count=0):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    db.add(
        SLOObservation(
            organization_id=organization_id,
            slo_key=slo_key,
            window_start=now,
            sample_count=len(samples),
            error_count=error_count,
            sum_value=Decimal(str(sum(samples))),
            bucket_bounds=BOUNDS,
            bucket_counts=_bucketise(samples),
        )
    )
    db.commit()


def _url(organization_id, slo_key: str | None = None) -> str:
    base = f"/api/v1/organizations/{organization_id}/slos"
    return f"{base}/{slo_key}" if slo_key else base


def test_list_returns_defaults_for_an_unconfigured_tenant(client: TestClient, tenant):
    response = client.get(_url(tenant.organization.id), headers=_auth(tenant.org_admin))
    assert response.status_code == 200

    body = response.json()
    assert body["organization_id"] == str(tenant.organization.id)
    assert body["entries"], "an unconfigured tenant still inherits platform targets"
    assert all(
        entry["target"]["source"] == "REGISTRY_DEFAULT" for entry in body["entries"]
    )


def test_list_reports_no_samples_as_null_not_zero(client: TestClient, tenant):
    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entry = next(e for e in body["entries"] if e["slo_key"] == "rag.retrieval.p95_ms")

    assert entry["observed_value"] is None
    assert entry["sample_count"] == 0
    assert entry["breached"] is False


def test_list_reports_live_compliance(client: TestClient, db_session, tenant):
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 950 + [900.0] * 50,
    )

    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entry = next(e for e in body["entries"] if e["slo_key"] == "rag.retrieval.p95_ms")

    assert entry["sample_count"] == 1000
    assert entry["breached"] is False
    assert entry["method"] == "HISTOGRAM_INTERPOLATED"


def test_method_discloses_that_latency_is_an_estimate(
    client: TestClient, db_session, tenant
):
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="rag.retrieval.p95_ms",
        samples=[40.0] * 100,
    )
    _observe(
        db_session,
        organization_id=tenant.organization.id,
        slo_key="api.availability",
        samples=[1.0] * 100,
    )

    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entries = {e["slo_key"]: e for e in body["entries"]}

    assert entries["rag.retrieval.p95_ms"]["method"] == "HISTOGRAM_INTERPOLATED"
    assert entries["api.availability"]["method"] == "EXACT"


def test_put_creates_a_tenant_override(client: TestClient, tenant):
    response = client.put(
        _url(tenant.organization.id, "rag.retrieval.p95_ms"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "150", "is_contractual": True, "notes": "Enterprise SLA"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["source"] == "ORGANIZATION"
    assert Decimal(body["target_value"]) == Decimal("150")
    assert body["is_contractual"] is True


def test_put_then_get_round_trips(client: TestClient, tenant):
    client.put(
        _url(tenant.organization.id, "rag.rerank.p95_ms"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "120"},
    )

    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entry = next(e for e in body["entries"] if e["slo_key"] == "rag.rerank.p95_ms")

    assert entry["target"]["source"] == "ORGANIZATION"
    assert Decimal(entry["target"]["target_value"]) == Decimal("120")


def test_put_rejects_an_unknown_key(client: TestClient, tenant):
    response = client.put(
        _url(tenant.organization.id, "rag.retrieval.p99_ms"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "100"},
    )
    assert response.status_code == 422


def test_put_rejects_a_ratio_above_one(client: TestClient, tenant):
    response = client.put(
        _url(tenant.organization.id, "api.availability"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "99.9"},
    )
    assert response.status_code == 400
    assert "0.999" in response.text


def test_put_cannot_change_the_unit(client: TestClient, tenant):
    client.put(
        _url(tenant.organization.id, "api.availability"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "0.99", "unit": "MILLISECONDS"},
    )

    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entry = next(e for e in body["entries"] if e["slo_key"] == "api.availability")
    assert entry["target"]["unit"] == "RATIO"


def test_delete_falls_back_to_the_platform_default(client: TestClient, tenant):
    client.put(
        _url(tenant.organization.id, "rag.retrieval.p95_ms"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "150"},
    )
    removed = client.delete(
        _url(tenant.organization.id, "rag.retrieval.p95_ms"),
        headers=_auth(tenant.org_admin),
    )
    assert removed.status_code == 204

    body = client.get(
        _url(tenant.organization.id), headers=_auth(tenant.org_admin)
    ).json()
    entry = next(e for e in body["entries"] if e["slo_key"] == "rag.retrieval.p95_ms")
    assert entry["target"]["source"] == "REGISTRY_DEFAULT"
    assert Decimal(entry["target"]["target_value"]) == Decimal("300")


def test_cross_tenant_read_is_404_with_no_leak(client: TestClient, db_session, tenant):
    _observe(
        db_session,
        organization_id=tenant.foreign_workspace.organization_id,
        slo_key="rag.retrieval.p95_ms",
        samples=[9999.0] * 500,
    )

    response = client.get(
        _url(tenant.foreign_workspace.organization_id),
        headers=_auth(tenant.org_admin),
    )
    assert response.status_code == 404
    assert str(tenant.foreign_workspace.organization_id) not in response.text
    assert "9999" not in response.text


def test_nonexistent_organization_is_indistinguishable(client: TestClient, tenant):
    real = client.get(
        _url(tenant.foreign_workspace.organization_id),
        headers=_auth(tenant.org_admin),
    )
    invented = client.get(_url(uuid.uuid4()), headers=_auth(tenant.org_admin))
    assert real.status_code == invented.status_code == 404


def test_cross_tenant_write_is_refused(client: TestClient, db_session, tenant):
    response = client.put(
        _url(tenant.foreign_workspace.organization_id, "rag.retrieval.p95_ms"),
        headers=_auth(tenant.org_admin),
        json={"target_value": "1"},
    )
    assert response.status_code == 404

    other = slo_service.resolve_slo_targets(
        db_session, tenant.foreign_workspace.organization_id
    )
    retrieval = next(r for r in other if r.slo_key == "rag.retrieval.p95_ms")
    assert retrieval.target_value == Decimal("300.0")


def test_a_plain_member_cannot_configure_slos(client: TestClient, tenant):
    response = client.put(
        _url(tenant.organization.id, "rag.retrieval.p95_ms"),
        headers=_auth(tenant.viewer),
        json={"target_value": "1"},
    )
    assert response.status_code in (403, 404)


def test_unauthenticated_access_is_refused(client: TestClient, tenant):
    assert client.get(_url(tenant.organization.id)).status_code in (401, 403)


def test_every_response_carries_a_request_id(client: TestClient, tenant):
    response = client.get(_url(tenant.organization.id), headers=_auth(tenant.org_admin))
    request_id = response.headers.get("X-Request-Id")

    assert request_id is not None
    assert len(request_id) == 32
    int(request_id, 16)


def test_an_inbound_traceparent_is_adopted(client: TestClient, tenant):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = client.get(
        _url(tenant.organization.id),
        headers={
            **_auth(tenant.org_admin),
            "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01",
        },
    )
    assert response.headers["X-Request-Id"] == trace_id


def test_a_malformed_traceparent_starts_a_fresh_trace(client: TestClient, tenant):
    response = client.get(
        _url(tenant.organization.id),
        headers={**_auth(tenant.org_admin), "traceparent": "not-a-traceparent"},
    )
    assert response.status_code == 200
    assert len(response.headers["X-Request-Id"]) == 32


def test_enqueue_stamps_the_ambient_trace_onto_the_job(db_session, tenant):
    from app.core.request_context import request_scope
    from app.models.job import Job
    from app.services import job_service

    with request_scope(organization_id=tenant.organization.id) as trace:
        job = job_service.enqueue(
            db_session,
            job_type="test.noop",
            payload={"hello": "world"},
            organization_id=tenant.organization.id,
            require_active_transaction=False,
        )
        expected = trace.request_id
    db_session.commit()

    stored = db_session.get(Job, job.id)
    assert stored.trace_id == expected
    assert stored.payload["_trace"]["trace_id"] == expected
    assert stored.payload["hello"] == "world"


def test_an_untraced_enqueue_leaves_trace_id_null(db_session, tenant):
    from app.models.job import Job
    from app.services import job_service

    job = job_service.enqueue(
        db_session,
        job_type="test.noop",
        organization_id=tenant.organization.id,
        require_active_transaction=False,
    )
    db_session.commit()

    assert db_session.get(Job, job.id).trace_id is None


def test_job_scope_rehydrates_an_inherited_trace():
    from app.core.request_context import get_trace_id, job_scope
    from app.services.job_service import trace_context_from

    trace_id = "a" * 32
    payload = {"_trace": {"trace_id": trace_id, "organization_id": str(uuid.uuid4())}}

    with job_scope(job_id=uuid.uuid4(), job_type="test.noop",
                   context=trace_context_from(payload)):
        assert get_trace_id() == trace_id
