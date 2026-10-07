"""Archiving an organization or a workspace, and undoing it.

"Archive" is documented as a reversible status change (organizations.py: "a
reversible status transition, not a removal"), and the "no access" page tells
people an owner or admin can restore it. Neither could:

* F-131 `POST /workspaces/{id}/restore` resolved the workspace through the same
  dependency that refuses an archived workspace, so it answered 403
  TENANT_SUSPENDED for the only workspaces it exists for. And the only Restore
  button sat on the archived workspace's own settings page, which cannot be
  opened.
* F-132 there was no way at all to restore an archived organization.
* F-133 `/me/context` named a workspace of an archived organization as the
  default when that organization came first, so sign-in landed on "no access".

What stays true while archived: every request into the organization or
workspace is refused (reads and writes), its API keys stay deactivated even
after a restore (an owner issues new ones on purpose), and people keep their
sign-in sessions, because those belong to the person, not the organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from tests.conftest import Fixture, _grant, _seat

pytestmark = pytest.mark.usefixtures("test_database")
API = "/api/v1"


@pytest.fixture()
def second_workspace(db_session: Session, tenant: Fixture) -> Workspace:
    """An organization keeps at least one active workspace, so archiving needs a second."""
    workspace = Workspace(
        organization_id=tenant.organization.id,
        slug=f"finance-{uuid.uuid4().hex[:6]}",
        workspace_name="Finance",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(workspace)
    db_session.commit()
    return workspace


def _archive_workspace(client: TestClient, tenant: Fixture) -> None:
    response = client.post(f"{API}/workspaces/{tenant.workspace.id}/archive", headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ARCHIVED"


def _archive_organization(client: TestClient, tenant: Fixture) -> None:
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/archive",
        json={"confirm_slug": tenant.organization.slug},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text


# =====================================================================
# Workspaces
# =====================================================================


def test_an_archived_workspace_refuses_reads_and_writes(client, tenant, second_workspace):
    _archive_workspace(client, tenant)
    read = client.get(f"{API}/workspaces/{tenant.workspace.id}/work-items", headers=tenant.owner.headers)
    assert read.status_code == 403
    write = client.patch(
        f"{API}/workspaces/{tenant.workspace.id}", json={"workspace_name": "Renamed"}, headers=tenant.owner.headers
    )
    assert write.status_code == 403


def test_an_archived_workspace_can_be_restored(client, tenant, second_workspace):
    _archive_workspace(client, tenant)

    restored = client.post(f"{API}/workspaces/{tenant.workspace.id}/restore", headers=tenant.owner.headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "ACTIVE"
    assert client.get(
        f"{API}/workspaces/{tenant.workspace.id}/work-items", headers=tenant.owner.headers
    ).status_code == 200


def test_an_organization_admin_may_restore_but_a_workspace_admin_may_not(client, tenant, second_workspace):
    _archive_workspace(client, tenant)

    refused = client.post(f"{API}/workspaces/{tenant.workspace.id}/restore", headers=tenant.ws_admin.headers)
    assert refused.status_code in (403, 404)
    outsider = client.post(
        f"{API}/workspaces/{tenant.workspace.id}/restore", headers=tenant.other_org_member.headers
    )
    assert outsider.status_code in (403, 404)

    allowed = client.post(f"{API}/workspaces/{tenant.workspace.id}/restore", headers=tenant.org_admin.headers)
    assert allowed.status_code == 200, allowed.text


def test_owners_and_admins_can_list_archived_workspaces_to_restore_them(client, tenant, second_workspace):
    _archive_workspace(client, tenant)
    url = f"{API}/organizations/{tenant.organization.id}/workspaces?include_archived=true"

    listed = {row["id"]: row["status"] for row in client.get(url, headers=tenant.org_admin.headers).json()}
    assert listed[str(tenant.workspace.id)] == "ARCHIVED"
    assert listed[str(second_workspace.id)] == "ACTIVE"

    # Without the flag, and for anyone else, the list stays what it was: active workspaces.
    plain = client.get(f"{API}/organizations/{tenant.organization.id}/workspaces", headers=tenant.org_admin.headers)
    assert str(tenant.workspace.id) not in {row["id"] for row in plain.json()}
    member = client.get(url, headers=tenant.contributor.headers)
    assert member.status_code == 200
    assert str(tenant.workspace.id) not in {row["id"] for row in member.json()}


# =====================================================================
# Organizations
# =====================================================================


def test_an_archived_organization_can_be_restored_by_its_owner(client, tenant, db_session):
    from tests.security.plans import put_on_plan

    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    key = client.post(
        f"{API}/organizations/{tenant.organization.id}/api-keys",
        json={"name": "ci", "scopes": ["work_items:read"]},
        headers=tenant.owner.headers,
    )
    assert key.status_code in (200, 201), key.text
    _archive_organization(client, tenant)
    assert client.get(
        f"{API}/workspaces/{tenant.workspace.id}/work-items", headers=tenant.owner.headers
    ).status_code == 403

    wrong = client.post(
        f"{API}/organizations/{tenant.organization.id}/restore",
        json={"confirm_slug": "not-the-slug"},
        headers=tenant.owner.headers,
    )
    assert wrong.status_code == 422
    restored = client.post(
        f"{API}/organizations/{tenant.organization.id}/restore",
        json={"confirm_slug": tenant.organization.slug},
        headers=tenant.owner.headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "ACTIVE"
    assert client.get(
        f"{API}/workspaces/{tenant.workspace.id}/work-items", headers=tenant.owner.headers
    ).status_code == 200

    # Keys deactivated by the archive stay deactivated: an owner issues new ones on purpose.
    listed = client.get(f"{API}/organizations/{tenant.organization.id}/api-keys", headers=tenant.owner.headers)
    assert listed.status_code == 200, listed.text
    assert listed.json() and all(row.get("deactivated_at") for row in listed.json())


def test_only_the_owner_may_restore_an_organization(client, tenant):
    _archive_organization(client, tenant)
    for persona in (tenant.org_admin, tenant.contributor, tenant.other_org_member):
        refused = client.post(
            f"{API}/organizations/{tenant.organization.id}/restore",
            json={"confirm_slug": tenant.organization.slug},
            headers=persona.headers,
        )
        assert refused.status_code in (403, 404), (persona, refused.text)


# =====================================================================
# Where sign-in lands
# =====================================================================


def test_sign_in_never_defaults_into_an_archived_organization(client, tenant, db_session):
    old = Organization(slug=f"old-{uuid.uuid4().hex[:8]}", name="Old Co", status=OrganizationStatus.ARCHIVED)
    db_session.add(old)
    db_session.flush()
    old_workspace = Workspace(
        organization_id=old.id, slug="main", workspace_name="Main", status=WorkspaceStatus.ACTIVE
    )
    db_session.add(old_workspace)
    db_session.flush()
    _seat(db_session, old, tenant.contributor, OrganizationRole.OWNER)
    _grant(db_session, old_workspace, tenant.contributor, WorkspaceRole.ADMIN)
    # The archived organization is the one joined first, so it heads the membership list.
    db_session.execute(
        update(OrganizationMember)
        .where(OrganizationMember.organization_id == old.id)
        .values(created_at=datetime.now(timezone.utc) - timedelta(days=30))
    )
    db_session.commit()

    context = client.get(f"{API}/me/context", headers=tenant.contributor.headers).json()
    statuses = {row["organization_id"]: row["organization_status"] for row in context["organizations"]}
    assert statuses[str(old.id)] == "ARCHIVED"  # still listed, so the picker can show it as archived
    assert context["default_organization_id"] == str(tenant.organization.id)
    assert context["default_workspace_id"] == str(tenant.workspace.id)


def test_members_do_not_see_an_archived_workspace_as_one_they_can_open(client, tenant, second_workspace):
    _archive_workspace(client, tenant)
    context = client.get(f"{API}/me/context", headers=tenant.contributor.headers).json()
    mine = next(row for row in context["organizations"] if row["organization_id"] == str(tenant.organization.id))
    assert str(tenant.workspace.id) not in {row["id"] for row in mine["workspaces"]}
    assert context["default_workspace_id"] != str(tenant.workspace.id)
