"""ARCH-13 Gate 13.8 — the verification review API."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.verification import DocumentVerification, VerificationStatus
from app.services import document_verification_service as dv

pytestmark = pytest.mark.usefixtures("test_database")


def _auth(persona) -> dict[str, str]:
    return {"Authorization": f"Bearer {persona.token}"}


def _verification(
    db: Session, tenant, work_item, *, status=VerificationStatus.PENDING, agents: int = 2
) -> DocumentVerification:
    verification = DocumentVerification(
        work_item_id=work_item.id,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        status=status,
        agent_count=agents,
    )
    db.add(verification)
    db.flush()
    return verification


@pytest.fixture()
def disagreed(db_session: Session, tenant, work_item_factory):
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus(
        [
            {"total": "1250.00", "vendor": "Acme"},
            {"total": "9999.00", "vendor": "Acme"},
        ]
    )
    for field_consensus in consensus.fields:
        db_session.add(field_consensus.as_row(verification.id))
    dv.triage(
        db_session,
        verification=verification,
        consensus=consensus,
        work_item=work_item,
    )
    db_session.commit()
    db_session.refresh(verification)

    assert verification.status is VerificationStatus.DISAGREED
    return verification


@pytest.fixture()
def agreed(db_session: Session, tenant, work_item_factory):
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus([{"total": "10"}, {"total": "10"}])
    for field_consensus in consensus.fields:
        db_session.add(field_consensus.as_row(verification.id))
    dv.triage(
        db_session,
        verification=verification,
        consensus=consensus,
        work_item=work_item,
    )
    db_session.commit()
    db_session.refresh(verification)
    return verification


def _base(tenant) -> str:
    return f"/api/v1/workspaces/{tenant.workspace.id}/verifications"


# =====================================================================
# The review queue
# =====================================================================


def test_review_queue_lists_disagreed(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.get(
        f"{_base(tenant)}?status=DISAGREED", headers=_auth(tenant.contributor)
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(disagreed.id)
    assert body[0]["status"] == "DISAGREED"
    assert body[0]["auto_approved"] is False


def test_status_filter_excludes_auto_approved(
    client: TestClient, tenant, disagreed, agreed
) -> None:
    response = client.get(
        f"{_base(tenant)}?status=DISAGREED", headers=_auth(tenant.contributor)
    )
    assert response.status_code == 200
    returned = {row["id"] for row in response.json()}
    assert returned == {str(disagreed.id)}
    assert str(agreed.id) not in returned


def test_detail_returns_the_per_field_breakdown(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.get(
        f"{_base(tenant)}/{disagreed.id}", headers=_auth(tenant.contributor)
    )
    assert response.status_code == 200, response.text

    fields = {f["field_path"]: f for f in response.json()["fields"]}
    assert fields["total"]["agreed"] is False
    assert fields["total"]["disagreement_kind"] == "CONFLICT"
    assert fields["total"]["agent_values"] == ["1250.00", "9999.00"]
    assert fields["vendor"]["agreed"] is True


# =====================================================================
# Tenancy
# =====================================================================


def test_cross_tenant_read_returns_404(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.get(
        f"/api/v1/workspaces/{tenant.foreign_workspace.id}/verifications/{disagreed.id}",
        headers=_auth(tenant.other_org_member),
    )
    assert response.status_code == 404
    assert "verification" in response.json()["detail"].lower()


def test_unknown_id_in_own_workspace_also_404(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.get(
        f"{_base(tenant)}/{uuid.uuid4()}", headers=_auth(tenant.contributor)
    )
    assert response.status_code == 404


def test_non_member_is_rejected(client: TestClient, tenant, disagreed) -> None:
    response = client.get(_base(tenant), headers=_auth(tenant.non_member))
    assert response.status_code in (403, 404)


def test_viewer_cannot_resolve(client: TestClient, tenant, disagreed) -> None:
    response = client.post(
        f"{_base(tenant)}/{disagreed.id}/resolve",
        headers=_auth(tenant.viewer),
        json={"values": {"total": "1250.00"}},
    )
    assert response.status_code == 403


def test_viewer_can_read_nothing_it_cannot_resolve(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.get(_base(tenant), headers=_auth(tenant.viewer))
    assert response.status_code == 403


# =====================================================================
# Resolve
# =====================================================================


def test_resolve_endpoint_releases_the_work_item(
    client: TestClient, db_session: Session, tenant, disagreed
) -> None:
    work_item_id = disagreed.work_item_id

    response = client.post(
        f"{_base(tenant)}/{disagreed.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {"total": "1250.00"}},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "REVIEWED"
    assert body["reviewed_by_user_id"] == str(tenant.contributor.user.id)
    assert body["reviewed_at"] is not None
    assert body["auto_approved"] is False

    resolved = {f["field_path"]: f for f in body["fields"]}
    assert resolved["total"]["resolved_value"] == "1250.00"
    assert resolved["total"]["consensus_value"] is not None

    db_session.expire_all()
    assert dv.blocking_verification(db_session, work_item_id=work_item_id) is None


def test_resolve_conflict_returns_409(
    client: TestClient, tenant, agreed
) -> None:
    response = client.post(
        f"{_base(tenant)}/{agreed.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {"total": "10"}},
    )
    assert response.status_code == 409, response.text
    assert "DISAGREED" in response.json()["detail"]


def test_empty_resolve_is_rejected(client: TestClient, tenant, disagreed) -> None:
    response = client.post(
        f"{_base(tenant)}/{disagreed.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {}},
    )
    assert response.status_code == 422


def test_partial_resolve_returns_409(
    client: TestClient, db_session: Session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus([{"a": "1", "b": "2"}, {"a": "9", "b": "8"}])
    for field_consensus in consensus.fields:
        db_session.add(field_consensus.as_row(verification.id))
    dv.triage(
        db_session, verification=verification, consensus=consensus, work_item=work_item
    )
    db_session.commit()

    response = client.post(
        f"{_base(tenant)}/{verification.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {"a": "1"}},
    )
    assert response.status_code == 409
    assert "unresolved" in response.json()["detail"]


def test_resolving_an_agreed_field_returns_409(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.post(
        f"{_base(tenant)}/{disagreed.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {"total": "1250.00", "vendor": "Hacked"}},
    )
    assert response.status_code == 409
    assert "not in disagreement" in response.json()["detail"]


def test_nested_resolved_value_is_rejected(
    client: TestClient, tenant, disagreed
) -> None:
    response = client.post(
        f"{_base(tenant)}/{disagreed.id}/resolve",
        headers=_auth(tenant.contributor),
        json={"values": {"total": {"amount": 1250}}},
    )
    assert response.status_code == 422
