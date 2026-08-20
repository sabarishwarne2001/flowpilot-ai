"""Gate 14.7 — the tenant usage metrics API."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import rollup_service
from tests.services.test_arch14_gate_14_2_rollups import emit

pytestmark = pytest.mark.usefixtures("test_database")


def _auth(persona) -> dict[str, str]:
    return {"Authorization": f"Bearer {persona.token}"}


@pytest.fixture()
def seeded(db_session: Session, tenant):
    now = datetime.now(timezone.utc)
    base = rollup_service.month_bucket(now) + timedelta(days=1)
    if base > now:
        base = now - timedelta(hours=6)

    for index in range(24):
        emit(
            db_session,
            org=tenant.organization.id,
            workspace_id=tenant.workspace.id,
            occurred_at=base + timedelta(minutes=index * 5),
            quantity=100,
            cost=200,
            estimated=index < 6,
        )
    for index in range(6):
        emit(
            db_session,
            org=tenant.organization.id,
            workspace_id=tenant.workspace.id,
            occurred_at=base + timedelta(minutes=index * 5),
            event_type="llm.output_token",
            quantity=40,
            cost=320,
        )
    emit(
        db_session,
        org=tenant.foreign_workspace.organization_id,
        occurred_at=base,
        quantity=99_999,
        cost=99_999,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.commit()
    return tenant


# ---------------------------------------------------------------------------
# Multi-tenant security gate
# ---------------------------------------------------------------------------


def test_cross_tenant_read_is_404_with_no_metadata(
    client: TestClient, seeded
):
    response = client.get(
        f"/api/v1/organizations/{seeded.foreign_workspace.organization_id}/usage/summary",
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 404

    body = response.text
    assert str(seeded.foreign_workspace.organization_id) not in body
    assert "99999" not in body


def test_nonexistent_organization_is_indistinguishable(
    client: TestClient, seeded
):
    real = client.get(
        f"/api/v1/organizations/{seeded.foreign_workspace.organization_id}/usage/summary",
        headers=_auth(seeded.org_admin),
    )
    invented = client.get(
        f"/api/v1/organizations/{uuid.uuid4()}/usage/summary",
        headers=_auth(seeded.org_admin),
    )
    assert real.status_code == invented.status_code == 404
    assert real.json().get("code") == invented.json().get("code")


def test_member_without_org_admin_is_refused(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        headers=_auth(seeded.contributor),
    )
    assert response.status_code in (403, 404)


def test_unauthenticated_is_refused(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary"
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------


def test_summary_returns_the_period(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        params={"period": "MONTH"},
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 200
    body = response.json()

    assert body["organization_id"] == str(seeded.organization.id)
    assert body["period"] == "MONTH"
    assert body["currency"] == "USD"

    lines = {line["event_type"]: line for line in body["lines"]}
    assert Decimal(lines["llm.input_token"]["quantity"]) == Decimal(2400)
    assert Decimal(lines["llm.output_token"]["quantity"]) == Decimal(240)
    assert lines["llm.input_token"]["unit"] == "token"
    assert body["total_cost_micros"] == 6_720
    assert body["as_of"] is not None


def test_estimated_usage_is_disclosed(client: TestClient, seeded):
    body = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        headers=_auth(seeded.org_admin),
    ).json()

    line = next(
        item for item in body["lines"] if item["event_type"] == "llm.input_token"
    )
    assert Decimal(line["estimated_quantity"]) == Decimal(600)
    assert line["estimated_cost_micros"] == 1_200
    assert Decimal(line["estimated_quantity"]) < Decimal(line["quantity"])
    assert body["estimated_cost_micros"] == 1_200


def test_summary_and_series_agree_to_the_micro(client: TestClient, seeded):
    org_id = seeded.organization.id
    summary = client.get(
        f"/api/v1/organizations/{org_id}/usage/summary",
        params={"period": "MONTH"},
        headers=_auth(seeded.org_admin),
    ).json()

    series = client.get(
        f"/api/v1/organizations/{org_id}/usage/series",
        params={
            "granularity": "DAY",
            "from": summary["period_start"],
            "to": summary["period_end"],
        },
        headers=_auth(seeded.org_admin),
    ).json()

    assert series["total_cost_micros"] == summary["total_cost_micros"]
    assert series["estimated_cost_micros"] == summary["estimated_cost_micros"]

    summed = sum(bucket["total_cost_micros"] for bucket in series["buckets"])
    assert summed == summary["total_cost_micros"]

    by_type: dict[str, Decimal] = {}
    for bucket in series["buckets"]:
        for line in bucket["lines"]:
            by_type[line["event_type"]] = by_type.get(
                line["event_type"], Decimal(0)
            ) + Decimal(line["quantity"])
    for line in summary["lines"]:
        assert by_type[line["event_type"]] == Decimal(line["quantity"])


def test_hourly_series_also_agrees(client: TestClient, seeded):
    org_id = seeded.organization.id
    summary = client.get(
        f"/api/v1/organizations/{org_id}/usage/summary",
        params={"period": "MONTH"},
        headers=_auth(seeded.org_admin),
    ).json()
    hourly = client.get(
        f"/api/v1/organizations/{org_id}/usage/series",
        params={
            "granularity": "HOUR",
            "from": summary["period_start"],
            "to": summary["period_end"],
        },
        headers=_auth(seeded.org_admin),
    )
    if hourly.status_code == 400:
        pytest.skip("month exceeds MAX_SERIES_BUCKETS at HOUR granularity")
    assert hourly.json()["total_cost_micros"] == summary["total_cost_micros"]


def test_workspace_scope_is_a_subset_of_the_organization(
    client: TestClient, seeded
):
    org_body = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        headers=_auth(seeded.org_admin),
    ).json()
    ws_body = client.get(
        f"/api/v1/workspaces/{seeded.workspace.id}/usage/summary",
        headers=_auth(seeded.ws_admin),
    ).json()

    assert ws_body["workspace_id"] == str(seeded.workspace.id)
    assert ws_body["total_cost_micros"] == org_body["total_cost_micros"]


def test_workspace_read_does_not_cross_tenants(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/workspaces/{seeded.foreign_workspace.id}/usage/summary",
        headers=_auth(seeded.ws_admin),
    )
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


def test_sealed_period_is_byte_identical_a_week_later(
    client: TestClient, db_session: Session, seeded
):
    org_id = seeded.organization.id
    now = datetime.now(timezone.utc)
    old_month = rollup_service.month_bucket(now) - timedelta(days=45)
    old_month = rollup_service.month_bucket(old_month)

    emit(
        db_session,
        org=org_id,
        occurred_at=old_month + timedelta(days=2),
        quantity=500,
        cost=1_000,
    )
    db_session.flush()
    rollup_service.run_rollup(db_session)
    rollup_service.seal_due(db_session, grace_hours=26)
    db_session.commit()

    label = old_month.strftime("%Y-%m")
    first = client.get(
        f"/api/v1/organizations/{org_id}/usage/summary",
        params={"period": "MONTH", "at": label},
        headers=_auth(seeded.org_admin),
    )
    assert first.status_code == 200
    assert first.json()["sealed"] is True
    assert first.json()["total_cost_micros"] == 1_000

    for index in range(10):
        emit(
            db_session,
            org=org_id,
            occurred_at=now - timedelta(minutes=index + 1),
            quantity=777,
            cost=7_777,
        )
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.commit()

    second = client.get(
        f"/api/v1/organizations/{org_id}/usage/summary",
        params={"period": "MONTH", "at": label},
        headers=_auth(seeded.org_admin),
    )
    assert second.status_code == 200
    assert second.content == first.content


def test_late_usage_is_disclosed_rather_than_hidden(
    client: TestClient, db_session: Session, seeded
):
    org_id = seeded.organization.id
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=40)

    emit(db_session, org=org_id, occurred_at=old, quantity=10, cost=10)
    db_session.flush()
    rollup_service.run_rollup(db_session)
    rollup_service.seal_due(db_session, grace_hours=26)
    db_session.flush()

    emit(db_session, org=org_id, occurred_at=old + timedelta(minutes=1), quantity=7, cost=70)
    db_session.flush()
    rollup_service.run_rollup(db_session)
    db_session.commit()

    body = client.get(
        f"/api/v1/organizations/{org_id}/usage/summary",
        headers=_auth(seeded.org_admin),
    ).json()

    line = next(
        item for item in body["lines"] if item["event_type"] == "llm.input_token"
    )
    assert Decimal(line["late_quantity"]) >= Decimal(7)
    assert line["late_cost_micros"] >= 70
    assert body["late_cost_micros"] >= 70


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_limits_reports_source_and_current_usage(client: TestClient, seeded):
    body = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/limits",
        headers=_auth(seeded.org_admin),
    ).json()

    assert body["organization_id"] == str(seeded.organization.id)
    assert body["limits"]

    for limit in body["limits"]:
        assert limit["source"] in {"ORGANIZATION", "TIER", "PLATFORM_DEFAULT"}
        assert limit["overage_policy"] in {
            "REFUSE",
            "ALLOW_AND_BILL",
            "ALLOW_AND_WARN",
        }
        assert limit["resets_at"] > limit["period_start"]


def test_limits_is_cross_tenant_safe(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.foreign_workspace.organization_id}/usage/limits",
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_month_shorthand_is_accepted(client: TestClient, seeded):
    label = datetime.now(timezone.utc).strftime("%Y-%m")
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        params={"period": "MONTH", "at": label},
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 200
    assert response.json()["period_start"].startswith(label)


def test_malformed_at_is_400_not_500(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/summary",
        params={"at": "last tuesday"},
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 400


def test_absurd_series_range_is_refused(client: TestClient, seeded):
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/series",
        params={
            "granularity": "HOUR",
            "from": "2020-01-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
        },
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 400


def test_inverted_range_is_refused(client: TestClient, seeded):
    now = datetime.now(timezone.utc)
    response = client.get(
        f"/api/v1/organizations/{seeded.organization.id}/usage/series",
        params={
            "granularity": "DAY",
            "from": now.isoformat(),
            "to": (now - timedelta(days=3)).isoformat(),
        },
        headers=_auth(seeded.org_admin),
    )
    assert response.status_code == 400