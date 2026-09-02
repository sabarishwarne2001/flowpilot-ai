"""
Tenant isolation gate for FlowPilot AI.

Asserts the failure table from ARCH-01 section B.5 across every tenant-scoped
route. This is a permanent CI gate, not a one-off verification: it must pass
before any change to routing, dependencies, or permissions merges.

WHAT IS ASSERTED

For a DENIED persona, the exact status code. For a PERMITTED persona, only
that authorization did not reject the request — a 409 from a business rule is
a pass, because that request cleared the boundary, which is what is under
test. Asserting business outcomes here would make the gate break on every
unrelated role change and, worse, train people to ignore its failures.

THE PROPERTY THAT MATTERS MOST

A non-member receives 404, not 403. A 403 confirms the tenant exists to
someone who cannot reach it, which is an enumeration oracle: an attacker could
walk a UUID space and learn which tenants are real. GitHub applies the same
rule to private repositories.

That distinction is invisible in normal use and easy to regress — someone
"tidying" an exception mapping turns a 404 into a 403 and nothing looks wrong.
It is exactly the kind of property that needs a test rather than a convention.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import Fixture, Persona

API = "/api/v1"

#: Statuses meaning the authorization layer rejected the request.
DENIED = {401, 403, 404}


def assert_denied(response, expected: int, label: str) -> None:
    assert response.status_code == expected, (
        f"{label}: expected {expected}, got {response.status_code}. "
        f"Body: {response.text[:200]}"
    )


def assert_allowed(response, label: str) -> None:
    assert response.status_code not in DENIED, (
        f"{label}: authorization rejected a permitted actor with "
        f"{response.status_code}. Body: {response.text[:200]}"
    )


# ===========================================================================
# The core property: 404, never 403, for non-members
# ===========================================================================

def test_non_member_gets_404_not_403_on_workspace(
    client: TestClient, tenant: Fixture
) -> None:
    """
    An authenticated user with no membership must not learn the workspace
    exists.
    """
    response = client.get(
        f"{API}/workspaces/{tenant.workspace.id}",
        headers=tenant.non_member.headers,
    )
    assert_denied(response, 404, "non-member GET workspace")
    assert response.status_code != 403, (
        "403 confirms the workspace exists to a non-member. This is an "
        "enumeration oracle and the single property this suite exists to "
        "protect."
    )


def test_foreign_org_member_gets_404_not_403(
    client: TestClient, tenant: Fixture
) -> None:
    """
    A legitimate user of another tenant is no more entitled than a stranger.

    This persona catches an authorization check written as "is this user
    authenticated" rather than "is this user a member of THIS tenant".
    """
    response = client.get(
        f"{API}/workspaces/{tenant.workspace.id}",
        headers=tenant.other_org_member.headers,
    )
    assert_denied(response, 404, "foreign-org member GET workspace")


def test_non_member_gets_404_on_organization(
    client: TestClient, tenant: Fixture
) -> None:
    for label, persona in (
        ("non-member", tenant.non_member),
        ("foreign-org member", tenant.other_org_member),
    ):
        response = client.get(
            f"{API}/organizations/{tenant.organization.id}",
            headers=persona.headers,
        )
        assert_denied(response, 404, f"{label} GET organization")


def test_nonexistent_tenant_is_indistinguishable_from_inaccessible(
    client: TestClient, tenant: Fixture
) -> None:
    """
    A real-but-unreachable workspace and one that does not exist must return
    the same status. Any difference reintroduces the oracle.
    """
    real = client.get(
        f"{API}/workspaces/{tenant.workspace.id}",
        headers=tenant.non_member.headers,
    )
    fake = client.get(
        f"{API}/workspaces/{uuid.uuid4()}",
        headers=tenant.non_member.headers,
    )
    assert real.status_code == fake.status_code == 404


def test_unauthenticated_gets_401_never_404(
    client: TestClient, tenant: Fixture
) -> None:
    """
    No session means 401 — never 404, and never a 200 that a guard might read
    as "this user has no tenant".

    This is the server half of the defect that sent expired sessions to the
    onboarding screen.
    """
    for path in (
        f"{API}/me/context",
        f"{API}/workspaces/{tenant.workspace.id}",
        f"{API}/organizations/{tenant.organization.id}",
    ):
        assert_denied(client.get(path), 401, f"unauthenticated {path}")


# ===========================================================================
# The full matrix
# ===========================================================================

READ_ROUTES = [
    "/workspaces/{workspace_id}",
    "/workspaces/{workspace_id}/members",
    "/workspaces/{workspace_id}/ai-settings/models",
]

#: (persona attribute, expected status for every read route)
#:
#: org_admin holds NO stored workspace grant. Their 200 proves derived
#: elevation resolves through the real dependency chain — a check reading a
#: stored membership would deny the most privileged account in the tenant.
READ_MATRIX = [
    ("owner", 200),
    ("org_admin", 200),
    ("ws_admin", 200),
    ("contributor", 200),
    ("viewer", 200),
    ("other_org_member", 404),
    ("non_member", 404),
]


@pytest.mark.parametrize("route", READ_ROUTES)
@pytest.mark.parametrize("persona_name,expected", READ_MATRIX)
def test_read_route_matrix(
    client: TestClient,
    tenant: Fixture,
    route: str,
    persona_name: str,
    expected: int,
) -> None:
    persona: Persona = getattr(tenant, persona_name)
    path = API + route.format(workspace_id=tenant.workspace.id)
    response = client.get(path, headers=persona.headers)

    label = f"{persona_name} GET {route}"
    
    # Workspace Viewer role is not permitted to view AI models (requires CONTRIBUTOR+)
    if "ai-settings" in route and persona_name == "viewer":
        assert_denied(response, 403, label)
        return

    if expected == 200:
        assert_allowed(response, label)
    else:
        assert_denied(response, expected, label)


#: Write routes. Denied personas get an exact code; permitted personas only
#: have to clear the boundary.
#:
#: The viewer/contributor 403 versus the non-member 404 is the second half of
#: the B.5 table: once an actor is known to be inside the tenant, acknowledging
#: it discloses nothing, so insufficient role is 403.
WRITE_MATRIX = [
    ("owner", None),
    ("org_admin", None),
    ("ws_admin", None),
    ("contributor", 403),
    ("viewer", 403),
    ("other_org_member", 404),
    ("non_member", 404),
]


@pytest.mark.parametrize("persona_name,expected", WRITE_MATRIX)
def test_workspace_update_requires_admin(
    client: TestClient, tenant: Fixture, persona_name: str, expected: int | None
) -> None:
    persona: Persona = getattr(tenant, persona_name)
    response = client.patch(
        f"{API}/workspaces/{tenant.workspace.id}",
        headers=persona.headers,
        json={"workspace_name": "Renamed"},
    )

    label = f"{persona_name} PATCH workspace"
    if expected is None:
        assert_allowed(response, label)
    else:
        assert_denied(response, expected, label)


# ARCH-04 Organization-scoped invitation writes matrix.
# Only Organization OWNER and ADMIN can issue organization invitations.
# ws_admin has organization role MEMBER, so they are denied (403).
INVITATION_WRITE_MATRIX = [
    ("owner", None),
    ("org_admin", None),
    ("ws_admin", 403),
    ("contributor", 403),
    ("viewer", 403),
    ("other_org_member", 404),
    ("non_member", 404),
]


@pytest.mark.parametrize("persona_name,expected", INVITATION_WRITE_MATRIX)
def test_invitation_creation_requires_admin(
    client: TestClient, tenant: Fixture, persona_name: str, expected: int | None
) -> None:
    persona: Persona = getattr(tenant, persona_name)
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/invitations",
        headers=persona.headers,
        json={
            "email": f"invitee-{uuid.uuid4().hex[:6]}@example.com",
            "organization_role": "MEMBER",
            "grants": [{"workspace_id": str(tenant.workspace.id), "role": "VIEWER"}],
        },
    )

    label = f"{persona_name} POST invitation"
    if expected is None:
        assert_allowed(response, label)
    else:
        assert_denied(response, expected, label)


ORG_ADMIN_MATRIX = [
    ("owner", None),
    ("org_admin", None),
    ("ws_admin", 403),
    ("contributor", 403),
    ("viewer", 403),
    ("other_org_member", 404),
    ("non_member", 404),
]


@pytest.mark.parametrize("persona_name,expected", ORG_ADMIN_MATRIX)
def test_workspace_creation_requires_org_admin(
    client: TestClient, tenant: Fixture, persona_name: str, expected: int | None
) -> None:
    """
    Creating a workspace is governed by ORGANIZATION role.

    ws_admin administers a workspace but is only an organization MEMBER, so
    they get 403 — a workspace does not create its siblings.
    """
    persona: Persona = getattr(tenant, persona_name)
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/workspaces",
        headers=persona.headers,
        json={"workspace_name": f"WS {uuid.uuid4().hex[:6]}"},
    )

    label = f"{persona_name} POST workspace"
    if expected is None:
        assert_allowed(response, label)
    else:
        assert_denied(response, expected, label)


def test_only_owner_can_archive_organization(
    client: TestClient, tenant: Fixture
) -> None:
    """Destroying the tenant is ownership-level, not delegable to an admin."""
    assert_denied(
        client.post(
            f"{API}/organizations/{tenant.organization.id}/archive",
            headers=tenant.org_admin.headers,
        ),
        403,
        "org_admin POST archive",
    )
    assert_allowed(
        client.post(
            f"{API}/organizations/{tenant.organization.id}/archive",
            headers=tenant.owner.headers,
        ),
        "owner POST archive",
    )


# ===========================================================================
# Cross-tenant identifier scoping
# ===========================================================================

def test_membership_id_from_another_workspace_is_rejected(
    client: TestClient, tenant: Fixture
) -> None:
    """
    A by-id lookup must be scoped to its tenant.

    Without the scope filter, an actor authorized for one workspace could
    address a membership in another by supplying its identifier — authorized
    for the wrong object.
    """
    foreign = client.get(
        f"{API}/workspaces/{tenant.foreign_workspace.id}/members",
        headers=tenant.other_org_member.headers,
    )
    assert foreign.status_code == 200
    foreign_membership_id = foreign.json()["items"][0]["id"]

    response = client.post(
        f"{API}/workspaces/{tenant.workspace.id}/members/{foreign_membership_id}/revoke",
        headers=tenant.owner.headers,
    )
    assert response.status_code in DENIED or response.status_code >= 400, (
        "A membership from another workspace was addressable through this one."
    )


def test_bootstrap_context_leaks_no_foreign_tenant(
    client: TestClient, tenant: Fixture
) -> None:
    """
    /me/context must report only tenants the actor belongs to.

    It is the single most sensitive response in the application: every guard,
    switcher, and page reads it, so anything that leaks here leaks everywhere.
    """
    response = client.get(f"{API}/me/context", headers=tenant.viewer.headers)
    assert response.status_code == 200

    body = response.json()
    org_ids = {org["organization_id"] for org in body["organizations"]}

    assert str(tenant.organization.id) in org_ids
    assert str(tenant.foreign_workspace.organization_id) not in org_ids

    workspace_ids = {
        ws["id"] for org in body["organizations"] for ws in org["workspaces"]
    }
    assert str(tenant.foreign_workspace.id) not in workspace_ids


def test_membership_less_user_gets_onboarding_signal_not_error(
    client: TestClient, tenant: Fixture
) -> None:
    """
    A user belonging to nothing gets 200 with requires_onboarding, never a 4xx.

    This is the contract that makes an expired session and a new user
    distinguishable. Conflating them is what sent session expiry to the
    workspace creation screen.
    """
    response = client.get(f"{API}/me/context", headers=tenant.non_member.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["requires_onboarding"] is True
    assert body["organizations"] == []
    assert body["default_workspace_id"] is None
