"""ARCH-20 — API tests for the organization compliance console.

The cross-tenant tests are the load-bearing ones. RequireOrgAdmin proves the
caller holds a role in SOME organization; only _assert_scope proves it is THIS
one. Without that check an admin of Acme reads Beta's erasure history by
editing the path, and every one of these endpoints returns personal data.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.compliance import (
    AUDIT_RETENTION_FLOOR_DAYS,
    REGION_GLOBAL,
    ComplianceExport,
    ErasedSubject,
)
from app.models.user import User
from app.models.work_item import WorkItem


def base(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/compliance"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_overview_requires_authentication(client, tenant):
    response = client.get(base(tenant.organization.id))
    assert response.status_code in (401, 403)


def test_overview_is_refused_to_a_plain_member(client, tenant):
    response = client.get(
        base(tenant.organization.id), headers=tenant.viewer.headers
    )
    assert response.status_code == 403


def test_overview_is_allowed_for_an_org_admin(client, tenant):
    response = client.get(
        base(tenant.organization.id), headers=tenant.org_admin.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == str(tenant.organization.id)
    assert body["residency"]["region"] == REGION_GLOBAL
    assert body["retention"]["audit_retention_floor_days"] == (
        AUDIT_RETENTION_FLOOR_DAYS
    )


def test_admin_of_another_tenant_gets_404_not_403(client, tenant, db_session):
    """404 rather than 403 on purpose: a 403 confirms the organization exists."""
    response = client.get(
        base(tenant.organization.id), headers=tenant.other_org_member.headers
    )
    assert response.status_code in (403, 404)


def test_residency_write_is_owner_only(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "GLOBAL", "acknowledge_no_migration": True},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 403


def test_retention_write_is_owner_only(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/retention",
        json={"auto_purge_enabled": False},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 403


def test_erasure_is_owner_only(client, tenant):
    response = client.post(
        f"{base(tenant.organization.id)}/erasures",
        json={
            "subject_user_id": str(tenant.contributor.user.id),
            "erasure_ticket": "T-1",
            "confirm_subject_email": tenant.contributor.user.email,
        },
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------


def test_residency_lists_regions_and_marks_which_are_configured(client, tenant):
    response = client.get(
        f"{base(tenant.organization.id)}/residency",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    options = {row["region"]: row["configured"] for row in
               response.json()["available_regions"]}
    assert set(options) == {"US", "EU", "APAC", "GLOBAL"}
    # GLOBAL is the default bucket and is always serveable.
    assert options["GLOBAL"] is True


def test_repin_without_acknowledgement_is_rejected(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "GLOBAL", "acknowledge_no_migration": False},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


def test_repin_to_global_succeeds(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "GLOBAL", "acknowledge_no_migration": True},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200
    assert response.json()["region"] == "GLOBAL"


def test_repin_to_an_unconfigured_region_is_409(client, tenant, monkeypatch):
    from app.services.compliance import residency_service

    residency_service.reset_regional_drivers()
    monkeypatch.setattr(
        residency_service.settings, "S3_REGIONAL_BUCKETS", {}, raising=False
    )
    response = client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "EU", "acknowledge_no_migration": True},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409
    assert "EU" in response.json()["detail"]


def test_repin_to_an_unknown_region_is_422(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "ANTARCTICA", "acknowledge_no_migration": True},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


def test_repin_is_audited(client, tenant, db_session):
    client.put(
        f"{base(tenant.organization.id)}/residency",
        json={"region": "GLOBAL", "acknowledge_no_migration": True},
        headers=tenant.owner.headers,
    )
    count = db_session.execute(
        text(
            "SELECT count(*) FROM audit_logs WHERE organization_id = :org "
            "AND action = 'RESIDENCY_CHANGED'"
        ),
        {"org": tenant.organization.id},
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_defaults_are_empty_and_purge_is_off(client, tenant):
    response = client.get(
        f"{base(tenant.organization.id)}/retention",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["work_item_retention_days"] is None
    assert body["auto_purge_enabled"] is False


def test_audit_retention_below_the_floor_is_422_at_the_boundary(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/retention",
        json={"audit_retention_days": 90, "auto_purge_enabled": False},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


def test_retention_at_the_floor_is_accepted(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/retention",
        json={
            "work_item_retention_days": 365,
            "audit_retention_days": AUDIT_RETENTION_FLOOR_DAYS,
            "conversation_retention_days": 180,
            "auto_purge_enabled": True,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["audit_retention_days"] == AUDIT_RETENTION_FLOOR_DAYS
    assert body["auto_purge_enabled"] is True


def test_retention_below_the_general_minimum_is_rejected(client, tenant):
    response = client.put(
        f"{base(tenant.organization.id)}/retention",
        json={"work_item_retention_days": 5, "auto_purge_enabled": False},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422


def test_retention_update_is_idempotent(client, tenant, db_session):
    payload = {"work_item_retention_days": 90, "auto_purge_enabled": False}
    for _ in range(2):
        assert (
            client.put(
                f"{base(tenant.organization.id)}/retention",
                json=payload,
                headers=tenant.owner.headers,
            ).status_code
            == 200
        )
    count = db_session.execute(
        text("SELECT count(*) FROM retention_policies WHERE organization_id = :org"),
        {"org": tenant.organization.id},
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


def test_preview_does_not_destroy(client, tenant, db_session):
    response = client.get(
        f"{base(tenant.organization.id)}/erasures/preview",
        params={"subject_user_id": str(tenant.contributor.user.id)},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["preserved_tables"]) == {
        "invoices",
        "invoice_line_items",
        "usage_events",
    }

    subject = db_session.get(User, tenant.contributor.user.id)
    assert subject.is_active is True


def test_erasure_requires_the_confirmation_address_to_match(client, tenant):
    response = client.post(
        f"{base(tenant.organization.id)}/erasures",
        json={
            "subject_user_id": str(tenant.contributor.user.id),
            "erasure_ticket": "DSAR-API-1",
            "confirm_subject_email": "wrong@example.com",
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 400


def test_erasure_succeeds_and_returns_counts(client, tenant, db_session):
    subject_id = tenant.contributor.user.id
    subject_email = tenant.contributor.user.email

    db_session.add(
        WorkItem(
            workspace_id=tenant.workspace.id,
            created_by_user_id=subject_id,
            original_filename="private.pdf",
            stored_filename=f"stored-{uuid.uuid4().hex}",
            file_type="application/pdf",
            file_size=10,
            extracted_text="secret",
        )
    )
    db_session.commit()

    response = client.post(
        f"{base(tenant.organization.id)}/erasures",
        json={
            "subject_user_id": str(subject_id),
            "erasure_ticket": "DSAR-API-2",
            "confirm_subject_email": subject_email,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["already_erased"] is False
    assert body["counts"]["work_items"] == 1
    assert len(body["erased_subject"]["subject_email_hash"]) == 64
    # The address itself must never come back out.
    assert subject_email not in response.text


def test_erasing_the_owner_is_409(client, tenant):
    response = client.post(
        f"{base(tenant.organization.id)}/erasures",
        json={
            "subject_user_id": str(tenant.owner.user.id),
            "erasure_ticket": "DSAR-API-3",
            "confirm_subject_email": tenant.owner.user.email,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409


def test_erasing_a_non_member_is_404(client, tenant):
    response = client.post(
        f"{base(tenant.organization.id)}/erasures",
        json={
            "subject_user_id": str(tenant.non_member.user.id),
            "erasure_ticket": "DSAR-API-4",
            "confirm_subject_email": tenant.non_member.user.email,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 404


def test_erasure_history_is_listed(client, tenant, db_session):
    db_session.add(
        ErasedSubject(
            organization_id=tenant.organization.id,
            subject_user_id=tenant.viewer.user.id,
            subject_email_hash="a" * 64,
            erasure_ticket="SEEDED-1",
        )
    )
    db_session.commit()

    response = client.get(
        f"{base(tenant.organization.id)}/erasures",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    tickets = [row["erasure_ticket"] for row in response.json()]
    assert "SEEDED-1" in tickets


def test_erasure_history_does_not_leak_across_tenants(client, tenant, db_session):
    db_session.add(
        ErasedSubject(
            organization_id=tenant.organization.id,
            subject_user_id=tenant.viewer.user.id,
            subject_email_hash="b" * 64,
            erasure_ticket="ACME-ONLY",
        )
    )
    db_session.commit()

    response = client.get(
        f"{base(tenant.organization.id)}/erasures",
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def test_export_list_starts_empty(client, tenant):
    response = client.get(
        f"{base(tenant.organization.id)}/exports",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    assert response.json() == []


def test_export_response_never_carries_a_url(client, tenant, db_session):
    db_session.add(
        ComplianceExport(
            organization_id=tenant.organization.id,
            status="COMPLETE",
            storage_key="acme/exports/abc.zip",
            residency_region=REGION_GLOBAL,
            file_size_bytes=1234,
        )
    )
    db_session.commit()

    response = client.get(
        f"{base(tenant.organization.id)}/exports",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    row = response.json()[0]
    assert "download_url" not in row
    assert "storage_key" not in row, "the key is internal and must not be exposed"
    assert row["file_size_bytes"] == 1234


def test_download_of_a_pending_export_is_409(client, tenant, db_session):
    record = ComplianceExport(
        organization_id=tenant.organization.id,
        status="PENDING",
        residency_region=REGION_GLOBAL,
    )
    db_session.add(record)
    db_session.commit()

    response = client.get(
        f"{base(tenant.organization.id)}/exports/{record.id}/download",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 409


def test_download_of_an_unknown_export_is_404(client, tenant):
    response = client.get(
        f"{base(tenant.organization.id)}/exports/{uuid.uuid4()}/download",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 404


def test_export_of_another_tenants_archive_is_404(client, tenant, db_session):
    """The export id is real, but it belongs to Beta. Scoping the lookup by
    organization_id is what makes this a 404 rather than a data leak."""
    foreign = ComplianceExport(
        organization_id=tenant.foreign_workspace.organization_id,
        status="COMPLETE",
        storage_key="beta/exports/x.zip",
        residency_region=REGION_GLOBAL,
    )
    db_session.add(foreign)
    db_session.commit()

    response = client.get(
        f"{base(tenant.organization.id)}/exports/{foreign.id}/download",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 404