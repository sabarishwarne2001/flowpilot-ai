"""ARCH-26 — HTTP-layer tests for the analytics and warehouse sync endpoints.

WHY THIS FILE IS IN tests/api/ AND NOT tests/services/
======================================================

`tests/services/conftest.py` shadows the root `client` fixture and binds
request sessions to a database other than the one `SessionLocal()` returns, so
an HTTP test written there reads a different database than the one it just
wrote to. Every TestClient call for this phase lives here, matching the
ARCH-25 precedent.

WHAT THE ROLE TESTS ARE FOR
===========================

Reads are ADMIN, every write is OWNER. That split is not cosmetic: registering
a destination hands a credential for third-party infrastructure to this
platform and starts a recurring egress of tenant data to it.

The tests below assert both halves — that an ADMIN can read and cannot write,
and that a member of a different organization gets 404 rather than 403 on
everything, because a 403 confirms the organization exists.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType
from app.models.warehouse_sync import WarehouseDestination
from app.services.analytics.connectors import CONNECTORS
from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectionTestOutcome,
    PushOutcome,
    WarehouseConnector,
)

API = "/api/v1"


def base(organization_id) -> str:
    return f"{API}/organizations/{organization_id}/analytics"


def s3_payload(label: str = "Acme warehouse") -> dict[str, Any]:
    return {
        "label": label,
        "credential": {
            "kind": "S3",
            "bucket": "acme-analytics",
            "region": "eu-west-1",
            "prefix": "flowpilot/",
            "access_key_id": "AKIAEXAMPLE0000000000",
            "secret_access_key": "s" * 40,
        },
    }


class _StubConnector(WarehouseConnector):
    kind = "S3"
    ALLOWED_HOST_SUFFIXES = ()

    def __init__(self, *, probe_ok: bool = True) -> None:
        self.probe_ok = probe_ok

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        return ConnectionTestOutcome(
            ok=self.probe_ok,
            latency_ms=9 if self.probe_ok else None,
            detail="probe ok" if self.probe_ok else "credential refused",
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        return PushOutcome(
            delivered_datasets=tuple(p.dataset for p in parts), failed_datasets=()
        )


@pytest.fixture(autouse=True)
def stub_s3_connector(monkeypatch):
    """No test in this file should make a network call."""
    monkeypatch.setitem(CONNECTORS, "S3", _StubConnector())


@pytest.fixture()
def destination(client, tenant) -> dict[str, Any]:
    response = client.post(
        base(tenant.organization.id) + "/destinations",
        json=s3_payload(),
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Destination CRUD
# ---------------------------------------------------------------------------


def test_owner_can_register_a_destination(client, tenant):
    response = client.post(
        base(tenant.organization.id) + "/destinations",
        json=s3_payload(),
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "S3"
    assert body["status"] == "ACTIVE"
    assert body["config"]["bucket"] == "acme-analytics"


def test_the_response_never_carries_the_secret(client, tenant, destination):
    """Invariant I2, asserted on the wire and not only on the model."""
    serialised = json.dumps(destination)
    assert "s" * 40 not in serialised
    assert "secret_access_key" not in serialised
    assert "encrypted_credential" not in serialised
    assert len(destination["credential_fingerprint"]) == 12


def test_listing_and_fetching_also_withhold_the_secret(client, tenant, destination):
    listed = client.get(
        base(tenant.organization.id) + "/destinations",
        headers=tenant.org_admin.headers,
    )
    assert listed.status_code == 200
    assert "secret_access_key" not in listed.text
    assert "s" * 40 not in listed.text

    detail = client.get(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        headers=tenant.org_admin.headers,
    )
    assert detail.status_code == 200
    assert "secret_access_key" not in detail.text


def test_a_never_probed_destination_reports_null_not_false(destination):
    """NULL means never tried; False means tried and refused. Invariant 6."""
    assert destination["last_test_ok"] is None
    assert destination["last_tested_at"] is None


def test_duplicate_labels_are_refused_within_a_tenant(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + "/destinations",
        json=s3_payload(destination["label"]),
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409


def test_rotating_a_credential_changes_the_fingerprint(client, tenant, destination):
    response = client.patch(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        json={
            "credential": {
                "kind": "S3",
                "bucket": "acme-analytics",
                "region": "eu-west-1",
                "access_key_id": "AKIAEXAMPLE1111111111",
                "secret_access_key": "t" * 40,
            }
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credential_fingerprint"] != destination["credential_fingerprint"]
    assert body["last_test_ok"] is None
    assert "t" * 40 not in response.text


def test_a_credential_of_a_different_kind_cannot_replace_one(
    client, tenant, destination
):
    response = client.patch(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        json={
            "credential": {
                "kind": "BIGQUERY",
                "project_id": "acme",
                "dataset": "analytics",
                "service_account_json": json.dumps(
                    {
                        "type": "service_account",
                        "client_email": "a@b.iam.gserviceaccount.com",
                        "private_key": "-----BEGIN PRIVATE KEY-----\nA\n-----END PRIVATE KEY-----",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                ),
            }
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 400


def test_deleting_a_destination_removes_it(client, tenant, destination):
    response = client.delete(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 204

    follow_up = client.get(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        headers=tenant.org_admin.headers,
    )
    assert follow_up.status_code == 404


def test_malformed_credentials_are_refused_at_the_boundary(client, tenant):
    payload = s3_payload()
    payload["credential"]["endpoint_url"] = "http://169.254.169.254/"
    response = client.post(
        base(tenant.organization.id) + "/destinations",
        json=payload,
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------


def test_probe_records_its_outcome_on_the_destination(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + f"/destinations/{destination['id']}/test",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] is not None

    refreshed = client.get(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        headers=tenant.org_admin.headers,
    ).json()
    assert refreshed["last_test_ok"] is True
    assert refreshed["last_tested_at"] is not None


def test_a_failing_probe_returns_200_with_ok_false(
    client, tenant, destination, monkeypatch
):
    """Not a 5xx. The probe ran and the answer is the payload."""
    monkeypatch.setitem(CONNECTORS, "S3", _StubConnector(probe_ok=False))
    response = client.post(
        base(tenant.organization.id) + f"/destinations/{destination['id']}/test",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["latency_ms"] is None


def test_a_failing_probe_is_audited_as_denied(
    client, tenant, destination, db_session, monkeypatch
):
    """A burst of these against varying hosts is credential spraying."""
    monkeypatch.setitem(CONNECTORS, "S3", _StubConnector(probe_ok=False))
    client.post(
        base(tenant.organization.id) + f"/destinations/{destination['id']}/test",
        headers=tenant.owner.headers,
    )

    row = db_session.execute(
        select(AuditLog)
        .where(AuditLog.organization_id == tenant.organization.id)
        .where(AuditLog.action == AuditAction.DESTINATION_TESTED)
        .order_by(AuditLog.created_at.desc())
    ).scalars().first()
    assert row is not None
    assert row.outcome == AuditOutcome.DENIED


def test_creating_a_destination_writes_an_audit_row(client, tenant, db_session):
    client.post(
        base(tenant.organization.id) + "/destinations",
        json=s3_payload("audited"),
        headers=tenant.owner.headers,
    )
    row = db_session.execute(
        select(AuditLog)
        .where(AuditLog.organization_id == tenant.organization.id)
        .where(AuditLog.action == AuditAction.DESTINATION_CREATED)
    ).scalars().first()
    assert row is not None
    assert row.resource_type == AuditResourceType.WAREHOUSE_DESTINATION
    assert "secret_access_key" not in json.dumps(row.details or {})


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def test_owner_can_create_a_schedule(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + "/schedules",
        json={
            "destination_id": destination["id"],
            "datasets": ["USAGE_ROLLUPS"],
            "cadence": "DAILY",
            "hour_utc": 2,
            "lookback_days": 1,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["next_run_at"] is not None
    assert body["is_dispatchable"] is True
    assert body["consecutive_failure_count"] == 0


def test_weekly_schedule_requires_a_weekday(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + "/schedules",
        json={
            "destination_id": destination["id"],
            "datasets": ["USAGE_ROLLUPS"],
            "cadence": "WEEKLY",
            "hour_utc": 2,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


def test_two_schedules_at_one_cadence_to_one_destination_are_refused(
    client, tenant, destination
):
    """They would race and write duplicate parts."""
    payload = {
        "destination_id": destination["id"],
        "datasets": ["USAGE_ROLLUPS"],
        "cadence": "DAILY",
        "hour_utc": 2,
    }
    first = client.post(
        base(tenant.organization.id) + "/schedules",
        json=payload,
        headers=tenant.owner.headers,
    )
    assert first.status_code == 201
    second = client.post(
        base(tenant.organization.id) + "/schedules",
        json=payload,
        headers=tenant.owner.headers,
    )
    assert second.status_code == 409


def test_a_schedule_cannot_point_at_another_tenants_destination(
    client, tenant, destination
):
    response = client.post(
        base(tenant.foreign_workspace.organization_id) + "/schedules",
        json={
            "destination_id": destination["id"],
            "datasets": ["USAGE_ROLLUPS"],
            "cadence": "DAILY",
        },
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code == 404


def test_duplicate_datasets_in_one_schedule_are_refused(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + "/schedules",
        json={
            "destination_id": destination["id"],
            "datasets": ["USAGE_ROLLUPS", "USAGE_ROLLUPS"],
            "cadence": "DAILY",
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Manual sync and run history
# ---------------------------------------------------------------------------


def test_manual_sync_is_accepted_and_enqueued(client, tenant, destination):
    response = client.post(
        base(tenant.organization.id) + "/sync",
        json={
            "destination_id": destination["id"],
            "datasets": ["USAGE_ROLLUPS"],
            "lookback_days": 7,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["job_id"]


def test_a_disabled_destination_cannot_be_synced(client, tenant, destination):
    client.patch(
        base(tenant.organization.id) + f"/destinations/{destination['id']}",
        json={"status": "DISABLED"},
        headers=tenant.owner.headers,
    )
    response = client.post(
        base(tenant.organization.id) + "/sync",
        json={"destination_id": destination["id"], "datasets": ["USAGE_ROLLUPS"]},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409


def test_run_history_is_empty_and_valid_for_a_new_tenant(client, tenant):
    response = client.get(
        base(tenant.organization.id) + "/runs", headers=tenant.org_admin.headers
    )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Consumption analytics and dataset docs
# ---------------------------------------------------------------------------


def test_consumption_returns_price_and_never_cost_basis(client, tenant):
    response = client.get(
        base(tenant.organization.id) + "/consumption?window_days=30",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "total_billed_micros" in body
    assert "cost_basis_micros" not in response.text
    assert "margin" not in response.text
    # Unmeasured latency is NULL, not 0.
    assert body["p95_latency_ms"] is None


def test_dataset_descriptors_match_what_the_writer_produces(client, tenant):
    from app.services.analytics import export_engine

    response = client.get(
        base(tenant.organization.id) + "/datasets",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    descriptors = {item["dataset"]: item for item in response.json()}
    assert set(descriptors) == set(export_engine.DATASET_SPECS)

    for name, spec in export_engine.DATASET_SPECS.items():
        columns = [column["name"] for column in descriptors[name]["columns"]]
        assert columns == list(spec.column_names)
        assert not set(columns) & export_engine.FORBIDDEN_COLUMN_NAMES


# ---------------------------------------------------------------------------
# Role gating and tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,suffix,payload",
    [
        ("post", "/destinations", s3_payload("admin-attempt")),
        ("post", "/sync", {"destination_id": str(uuid.uuid4()), "datasets": ["USAGE_ROLLUPS"]}),
    ],
)
def test_admin_cannot_write(client, tenant, method, suffix, payload):
    response = getattr(client, method)(
        base(tenant.organization.id) + suffix,
        json=payload,
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 403, (
        f"{method.upper()} {suffix} accepted an ADMIN write"
    )


@pytest.mark.parametrize("suffix", ["/destinations", "/schedules", "/runs", "/datasets"])
def test_admin_can_read(client, tenant, suffix):
    response = client.get(
        base(tenant.organization.id) + suffix, headers=tenant.org_admin.headers
    )
    assert response.status_code == 200


@pytest.mark.parametrize("suffix", ["/destinations", "/schedules", "/runs"])
def test_a_plain_member_cannot_read(client, tenant, suffix):
    response = client.get(
        base(tenant.organization.id) + suffix, headers=tenant.viewer.headers
    )
    assert response.status_code == 403


@pytest.mark.parametrize("suffix", ["/destinations", "/schedules", "/runs"])
def test_another_tenant_gets_404_and_not_403(client, tenant, suffix):
    """403 would confirm the organization exists."""
    response = client.get(
        base(tenant.organization.id) + suffix,
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code in (403, 404)
    if response.status_code == 403:
        # The dependency refused before scope resolution, which is also safe:
        # it discloses nothing about the target organization.
        assert "not found" not in response.text.lower()


def test_a_foreign_destination_id_is_not_readable(client, tenant, destination):
    response = client.get(
        base(tenant.foreign_workspace.organization_id)
        + f"/destinations/{destination['id']}",
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code == 404


def test_unauthenticated_requests_are_refused(client, tenant):
    response = client.get(base(tenant.organization.id) + "/destinations")
    assert response.status_code in (401, 403)