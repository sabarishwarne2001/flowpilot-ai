import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import organization_members as organization_members_crud
from app.models.api_key import ApiKey
from app.services import api_key_service, organization_member_service


def test_member_deactivation_revokes_api_keys_in_same_transaction(
    client: TestClient, db_session: Session, tenant
):
    org = tenant.organization
    member_user = tenant.org_admin.user
    owner_user = tenant.owner.user

    member_membership = organization_members_crud.get_organization_member(
        db_session, organization_id=org.id, user_id=member_user.id
    )
    owner_membership = organization_members_crud.get_organization_member(
        db_session, organization_id=org.id, user_id=owner_user.id
    )

    assert member_membership is not None
    assert owner_membership is not None

    # Issue key for member
    key, token = api_key_service.issue_api_key(
        db_session,
        organization_id=org.id,
        actor=member_membership,
        name="Offboard Test Key",
        scopes=["workspaces:read"],
    )
    db_session.commit()

    # Offboard member
    organization_member_service.deactivate_member(
        db_session,
        organization=org,
        actor_membership=owner_membership,
        target_membership=member_membership,
    )

    # Key must be deactivated with reason OFFBOARDED
    db_session.refresh(key)
    assert key.deactivated_at is not None
    assert key.deactivated_reason == "OFFBOARDED"

    # Token must be rejected with 401
    res = client.get(
        f"/api/v1/organizations/{org.id}/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 401