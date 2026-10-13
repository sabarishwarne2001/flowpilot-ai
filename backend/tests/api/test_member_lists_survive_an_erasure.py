"""Member lists still load after someone is erased (campaign session 3, F-326).

Erasing a data subject anonymises their account: the address becomes
`erased+<id>@erased.invalid`, a reserved domain so nothing can ever be delivered to it. The
member lists embed each person's address through `UserSummary`, which validated it as an email
address on the way out and rejects reserved domains, so once anyone had been erased the
organization's member list with former members (`include_inactive=true`, which the erasure form
and the members page use) answered 500, and so did a workspace's member list while the erased
person still held a grant. An address read from the database is not re-validated on the way out.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import Fixture

API = "/api/v1"


def _erase(client: TestClient, tenant: Fixture) -> str:
    subject = tenant.contributor.user
    erased = client.post(
        f"{API}/organizations/{tenant.organization.id}/compliance/erasures",
        json={"subject_user_id": str(subject.id), "erasure_ticket": "DSAR-F326",
              "confirm_subject_email": subject.email},
        headers=tenant.owner.headers,
    )
    assert erased.status_code in (200, 201), erased.text
    return str(subject.id)


def test_the_organization_member_list_with_former_members_loads_after_an_erasure(
    client: TestClient, tenant: Fixture
) -> None:
    subject_id = _erase(client, tenant)
    listed = client.get(f"{API}/organizations/{tenant.organization.id}/members", params={"include_inactive": True},
                        headers=tenant.owner.headers)
    assert listed.status_code == 200, listed.text
    erased = [row for row in listed.json()["items"] if row["user"]["id"] == subject_id]
    assert erased, "the erased person is still listed as a former member"
    assert erased[0]["user"]["email"].endswith("@erased.invalid")


def test_a_workspace_member_list_loads_after_an_erasure(client: TestClient, tenant: Fixture) -> None:
    _erase(client, tenant)
    listed = client.get(f"{API}/workspaces/{tenant.workspace.id}/members", headers=tenant.owner.headers)
    assert listed.status_code == 200, listed.text
