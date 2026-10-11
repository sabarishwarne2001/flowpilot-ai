"""Nobody raises their own access, and no one below the owner can unseat one (campaign session 1, E.3).

    pytest tests/engines/test_role_escalation_live.py -q

`tests/security/test_role_matrix.py` sweeps every route against every persona below the
role it requires. These are the cases a rank sweep cannot see, because the caller holds
the role the route requires and the harm is in the target or the body:

  * an organization admin making themselves owner, or demoting or removing an owner;
  * a member changing their own organization role;
  * a workspace admin who is an organization member raising their own organization role
    through a workspace route, or a contributor raising their own workspace role;
  * a key issued by an admin keeps the admin's reach after the admin is demoted;
  * the Billing role (a finance contact) reaching documents.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.organization import OrganizationMember, OrganizationRole
from app.models.workspace import WorkspaceMember
from tests.engines.conftest import Engines

REFUSED = (403, 404)


def _org_membership(engines: Engines, persona) -> str:
    engines.refresh()
    return str(engines.db.execute(select(OrganizationMember.id).where(
        OrganizationMember.organization_id == engines.org,
        OrganizationMember.user_id == persona.user.id)).scalar_one())


def _org_role(engines: Engines, persona) -> OrganizationRole:
    engines.refresh()
    return engines.db.execute(select(OrganizationMember.role).where(
        OrganizationMember.organization_id == engines.org,
        OrganizationMember.user_id == persona.user.id)).scalar_one()


def _ws_membership(engines: Engines, persona) -> WorkspaceMember:
    engines.refresh()
    return engines.db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == engines.tenant.workspace.id,
        WorkspaceMember.user_id == persona.user.id)).scalar_one()


def test_an_admin_cannot_make_themselves_owner(engines: Engines) -> None:
    admin = engines.tenant.org_admin
    response = engines.patch(f"/members/{_org_membership(engines, admin)}", {"role": "OWNER"}, org=True, as_user=admin)
    assert response.status_code == 400, response.text
    assert "cannot change your own role" in response.json()["message"]
    assert _org_role(engines, admin) == OrganizationRole.ADMIN


def test_an_admin_cannot_demote_or_remove_an_owner(engines: Engines) -> None:
    t = engines.tenant
    owner = _org_membership(engines, t.owner)
    demoted = engines.patch(f"/members/{owner}", {"role": "MEMBER"}, org=True, as_user=t.org_admin)
    assert demoted.status_code in REFUSED, demoted.text
    removed = engines.post(f"/members/{owner}/deactivate", org=True, as_user=t.org_admin)
    assert removed.status_code in REFUSED, removed.text
    assert _org_role(engines, t.owner) == OrganizationRole.OWNER


def test_a_member_cannot_change_their_own_organization_role(engines: Engines) -> None:
    for persona in (engines.tenant.ws_admin, engines.tenant.contributor, engines.tenant.viewer):
        response = engines.patch(
            f"/members/{_org_membership(engines, persona)}", {"role": "ADMIN"}, org=True, as_user=persona
        )
        assert response.status_code in REFUSED, response.text
        assert _org_role(engines, persona) == OrganizationRole.MEMBER


def test_a_contributor_cannot_raise_their_own_workspace_role(engines: Engines) -> None:
    contributor = engines.tenant.contributor
    grant = _ws_membership(engines, contributor)
    response = engines.patch(f"/members/{grant.id}", {"role": "ADMIN"}, as_user=contributor)
    assert response.status_code in REFUSED, response.text
    assert _ws_membership(engines, contributor).role.value == "CONTRIBUTOR"


def test_a_key_loses_what_its_issuer_loses(engines: Engines) -> None:
    """A key never reaches further than the person who issued it can reach today."""
    admin = engines.tenant.org_admin
    issued = engines.client.post(
        f"/api/v1/organizations/{engines.org}/api-keys",
        json={"name": "audit reader", "scopes": ["audit_logs:read"]},
        headers=admin.headers,
    )
    assert issued.status_code == 201, issued.text
    key = {"Authorization": f"Bearer {issued.json()['token']}"}
    audit = f"/api/v1/organizations/{engines.org}/audit-logs"
    assert engines.client.get(audit, headers=key).status_code == 200

    demoted = engines.patch(
        f"/members/{_org_membership(engines, admin)}", {"role": "MEMBER"}, org=True, as_user=engines.tenant.owner
    )
    assert demoted.status_code == 200, demoted.text
    assert engines.client.get(audit, headers=key).status_code in (401, *REFUSED)


def test_the_billing_role_sees_the_money_and_not_the_documents(engines: Engines) -> None:
    """BILLING is a finance contact: plan, usage and invoices; no workspace, no keys, no team changes."""
    t = engines.tenant
    engines.refresh()
    membership = engines.db.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == engines.org, OrganizationMember.user_id == t.viewer.user.id)).scalar_one()
    membership.role = OrganizationRole.BILLING
    grant = engines.db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == t.workspace.id, WorkspaceMember.user_id == t.viewer.user.id)).scalar_one()
    engines.db.delete(grant)
    engines.db.commit()
    finance = t.viewer

    assert engines.get("/work-items", as_user=finance).status_code in REFUSED
    assert engines.get("/billing/access-summary", org=True, as_user=finance).status_code == 200
    assert engines.get("/billing/subscription", org=True, as_user=finance).status_code == 200
    key = engines.client.post(
        f"/api/v1/organizations/{engines.org}/api-keys",
        json={"name": "finance", "scopes": ["billing:read"]},
        headers=finance.headers,
    )
    assert key.status_code in REFUSED, key.text
    contributor = _org_membership(engines, t.contributor)
    assert engines.patch(f"/members/{contributor}", {"role": "ADMIN"}, org=True, as_user=finance).status_code in REFUSED
    assert engines.post(f"/members/{contributor}/deactivate", org=True, as_user=finance).status_code in REFUSED
