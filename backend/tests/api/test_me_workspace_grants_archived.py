"""Settings -> Profile -> "Your workspace access" and archived tenants (F-142).

Found on a running server: `/me/workspaces` dropped every grant on an archived
workspace and never looked at the organization, so a grant on a workspace whose
organization was archived was listed exactly like an active one, and a grant on
an archived workspace vanished. The list now carries both statuses and an
`archived` flag the panel turns into an ARCHIVED badge.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.organization import OrganizationStatus
from app.models.workspace import WorkspaceStatus

URL = "/api/v1/me/workspaces"


def _grants(client: TestClient, persona) -> dict[str, dict]:
    response = client.get(URL, headers=persona.headers)
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()}


def test_an_active_grant_is_not_archived(client: TestClient, tenant) -> None:
    row = _grants(client, tenant.contributor)[str(tenant.workspace.id)]
    assert row["status"] == "ACTIVE"
    assert row["organization_status"] == "ACTIVE"
    assert row["organization_name"] == tenant.organization.name
    assert row["archived"] is False


def test_a_grant_in_an_archived_organization_is_marked_archived(
    client: TestClient, db_session: Session, tenant
) -> None:
    tenant.organization.status = OrganizationStatus.ARCHIVED
    db_session.commit()

    row = _grants(client, tenant.contributor)[str(tenant.workspace.id)]
    assert row["status"] == "ACTIVE"
    assert row["organization_status"] == "ARCHIVED"
    assert row["archived"] is True


def test_a_grant_on_an_archived_workspace_is_listed_and_marked_archived(
    client: TestClient, db_session: Session, tenant
) -> None:
    tenant.workspace.status = WorkspaceStatus.ARCHIVED
    db_session.commit()

    grants = _grants(client, tenant.contributor)
    assert str(tenant.workspace.id) in grants, "the grant on the archived workspace disappeared"
    row = grants[str(tenant.workspace.id)]
    assert row["status"] == "ARCHIVED"
    assert row["archived"] is True
