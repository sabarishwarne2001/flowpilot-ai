"""IDOR (soft) — a document outside THIS workspace is a 404 on its child routes.

    pytest tests/security/test_work_item_child_routes_are_workspace_scoped.py -q

`GET /work-items/{id}/entities` and `GET /work-items/{id}/extraction-memory`
filtered their queries by workspace, so no data crossed a tenant boundary, but a
foreign or unknown document id answered 200 with an empty result while every
sibling route answers 404. Found by the child-entity IDOR sweep; fixed so a
document that is not in the caller's workspace is indistinguishable from one
that does not exist, and the response is the conventional 404.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.work_item import WorkItem
from tests.security.plans import put_on_plan

ROUTES = ("entities", "extraction-memory")


@pytest.fixture()
def enterprise_tenants(db_session, tenant):
    put_on_plan(db_session, tenant.organization, "enterprise")
    put_on_plan(db_session, tenant.foreign_workspace.organization, "enterprise")


def _foreign_item(db_session, tenant) -> WorkItem:
    item = WorkItem(
        workspace_id=tenant.foreign_workspace.id,
        created_by_user_id=tenant.other_org_member.user.id,
        original_filename="beta.pdf",
        stored_filename=f"{tenant.foreign_workspace.organization_id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=10,
        status="PROCESSED",
        extracted_entities={},
        extraction_metadata={},
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.mark.parametrize("leaf", ROUTES)
def test_another_tenants_document_is_a_404(client, db_session, tenant, enterprise_tenants, leaf) -> None:
    item = _foreign_item(db_session, tenant)
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items/{item.id}/{leaf}",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 404, response.text[:200]


@pytest.mark.parametrize("leaf", ROUTES)
def test_an_unknown_document_is_the_same_404(client, tenant, enterprise_tenants, leaf) -> None:
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items/{uuid.uuid4()}/{leaf}",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("leaf", ROUTES)
def test_the_workspaces_own_document_still_answers_200(
    client, tenant, enterprise_tenants, work_item_factory, leaf
) -> None:
    """Control: a document with no entities or memory yet is 200, not 404."""
    item = work_item_factory()
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items/{item.id}/{leaf}",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text[:200]
