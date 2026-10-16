"""An API key works only inside the organization that issued it, on real Postgres.

A key is issued, listed, limited and revoked in ONE organization, and its scopes are worked out from
its issuer's role THERE. Before this test, a person who belonged to two organizations could issue a
key in one and use it on the other: every organization route checked only that the person was a
member of the organization in the path, never that the key was issued there. That key got round the
other organization's sign-in rules (single sign-on and two-factor do not apply to keys), its plan
(the developer API was checked against the issuing organization) and its administrators, who could
neither see nor revoke it.

The key is refused as if the organization did not exist (404), the answer a stranger gets.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.services import api_key_service
from tests.conftest import Fixture, _seat
from tests.security.plans import put_on_plan

API = "/api/v1"


def _foreign_key(db: Session, tenant: Fixture) -> tuple[Organization, dict[str, str]]:
    """A key issued in the OTHER organization by its owner, who is also an admin of the tenant's."""
    other = db.get(Organization, tenant.foreign_workspace.organization_id)
    put_on_plan(db, tenant.organization, "enterprise")
    put_on_plan(db, other, "enterprise")
    _seat(db, tenant.organization, tenant.other_org_member, OrganizationRole.ADMIN)
    db.commit()
    issuer = db.query(OrganizationMember).filter_by(organization_id=other.id,
                                                    user_id=tenant.other_org_member.user.id).one()
    _row, token = api_key_service.issue_api_key(db, organization_id=other.id, actor=issuer, name="other org's key",
                                                scopes=["organizations:read", "workspaces:read", "members:read",
                                                        "work_items:read", "webhooks:read"])
    db.commit()
    return other, {"Authorization": f"Bearer {token}"}


def test_a_key_is_refused_by_an_organization_that_did_not_issue_it(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    other, headers = _foreign_key(db_session, tenant)
    org = tenant.organization.id
    for path in (f"/organizations/{org}", f"/organizations/{org}/workspaces", f"/organizations/{org}/members",
                 f"/organizations/{org}/webhooks/endpoints", f"/workspaces/{tenant.workspace.id}/work-items"):
        response = client.get(f"{API}{path}", headers=headers)
        assert response.status_code == 404, (path, response.status_code, response.text[:200])
        assert "Engineering" not in response.text, (path, response.text[:200])

    # The same key still works where it was issued, and its issuer still reaches the tenant signed in.
    own = client.get(f"{API}/organizations/{other.id}/workspaces", headers=headers)
    assert own.status_code == 200, own.text
    signed_in = client.get(f"{API}/organizations/{org}/workspaces", headers=tenant.other_org_member.headers)
    assert signed_in.status_code == 200, signed_in.text
