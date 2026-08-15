import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import organization_members as organization_members_crud
from app.services import api_key_service


def test_api_key_rate_limit_identity_resolution(client: TestClient, db_session: Session, tenant):
    org_id = tenant.organization.id

    member = organization_members_crud.get_organization_member(
        db_session, organization_id=org_id, user_id=tenant.org_admin.user.id
    )
    assert member is not None

    # Issue API Key
    key, token = api_key_service.issue_api_key(
        db_session,
        organization_id=org_id,
        actor=member,
        name="Rate Limit Key",
        scopes=["workspaces:read"],
    )
    db_session.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # Verify per-key rate limit headers are attached
    res = client.get(
        f"/api/v1/organizations/{org_id}/workspaces",
        headers=headers,
    )
    assert res.status_code == 200
    assert "RateLimit-Limit" in res.headers
    assert res.headers["RateLimit-Limit"] == "600"