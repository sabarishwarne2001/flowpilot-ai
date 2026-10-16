"""The sample company's workspace is a demonstration: it never uses up the plan's workspaces
(`limit.workspaces`), on any plan."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceStatus
from tests.security.plans import put_on_plan


def _sample(db: Session, organization_id) -> None:
    db.add(Workspace(organization_id=organization_id, slug="sample-meridian", workspace_name="Meridian (sample)",
                     status=WorkspaceStatus.ACTIVE, is_sample=True))
    db.commit()


def test_a_sample_workspace_does_not_use_up_the_free_plans_one_workspace(client, db_session: Session, tenant):
    put_on_plan(db_session, tenant.organization, "free")
    _sample(db_session, tenant.organization.id)
    # Free includes one workspace and "engineering" is it: a second real one is refused, sample or not.
    refused = client.post(f"/api/v1/organizations/{tenant.organization.id}/workspaces",
                          json={"workspace_name": "Second"}, headers=tenant.owner.headers)
    assert refused.status_code == 402, refused.text
    assert refused.json()["details"]["requested"] == 2


def test_with_room_for_one_more_the_sample_does_not_take_it(client, db_session: Session, tenant):
    put_on_plan(db_session, tenant.organization, "developer")
    from app.services import quota_service

    limit = int(quota_service.plan_limit(db_session, organization_id=tenant.organization.id,
                                         limit_key="limit.workspaces"))
    for n in range(limit - 2):  # leave room for exactly one more real workspace
        db_session.add(Workspace(organization_id=tenant.organization.id, slug=f"extra-{n}",
                                 workspace_name=f"Extra {n}", status=WorkspaceStatus.ACTIVE))
    db_session.commit()
    _sample(db_session, tenant.organization.id)
    created = client.post(f"/api/v1/organizations/{tenant.organization.id}/workspaces",
                          json={"workspace_name": "Last one"}, headers=tenant.owner.headers)
    assert created.status_code == 201, created.text
