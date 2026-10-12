"""Who can hold work follows who can reach the workspace (campaign session 3, N-063, F-296).

Organization owners and administrators administer every workspace of their organization without a
stored grant (`resolve_effective_workspace_role`), yet review items and obligations could only be
given to someone with a stored workspace grant: assigning a review item to an organization admin
was refused ("not a member of this workspace"), and so was making them an obligation's owner. And
the other way round, a person whose grant was revoked stayed assignable (the assignee list read
every grant, revoked ones included) and kept the items they held, out of their reach.

Now both follow effective access, as calendar feeds do since F-249: an owner or admin can hold
items in any workspace; a revoked or removed person is not offered, cannot be given an item, and
loses the items they held (back to unassigned).
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_review_hub_live import _held


def _one_item(engines: Engines) -> str:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    _held(engines, 91)
    queue = engines.get("/review", params={"kind": "EXTRACTION"}).json()
    return queue["items"][0]["item_id"]


def _assignees(engines: Engines) -> set[str]:
    return {row["user_id"] for row in engines.get("/review/assignees").json()}


def test_an_organization_admin_without_a_grant_can_be_assigned_a_review_item(engines: Engines) -> None:
    item = _one_item(engines)
    admin = str(engines.tenant.org_admin.user.id)
    assert admin in _assignees(engines), "an organization admin administers the workspace"
    assigned = engines.post(f"/review/EXTRACTION/{item}/assign", {"assignee_user_id": admin})
    assert assigned.status_code == 204, assigned.text


def test_someone_outside_the_workspace_still_cannot_be_assigned(engines: Engines) -> None:
    item = _one_item(engines)
    outsider = str(engines.tenant.other_org_member.user.id)
    refused = engines.post(f"/review/EXTRACTION/{item}/assign", {"assignee_user_id": outsider})
    assert refused.status_code in (400, 409, 422), refused.text


def test_a_revoked_member_is_not_offered_and_loses_what_they_held(engines: Engines) -> None:
    item = _one_item(engines)
    contributor = engines.tenant.contributor.user
    assert engines.post(f"/review/EXTRACTION/{item}/assign", {"assignee_user_id": str(contributor.id)}).status_code == 204

    grants = {row["user"]["email"]: row["id"] for row in engines.get("/members").json()["items"] if row["id"]}
    assert engines.post(f"/members/{grants[contributor.email]}/revoke").status_code == 200

    assert str(contributor.id) not in _assignees(engines), "a revoked grant is not someone to give work to"
    queue = {row["item_id"]: row for row in engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]}
    assert queue[item].get("assignee_user_id") in (None, ""), queue[item]
    again = engines.post(f"/review/EXTRACTION/{item}/assign", {"assignee_user_id": str(contributor.id)})
    assert again.status_code in (400, 409, 422), again.text


def test_an_organization_admin_can_own_an_obligation(engines: Engines) -> None:
    engines.plan("business")
    created = engines.post("/obligations", {
        "kind": "PAYMENT", "title": "Quarterly VAT", "rule": {"kind": "FIXED", "date": "2026-12-15"},
        "owner_user_id": str(engines.tenant.org_admin.user.id),
    })
    assert created.status_code in (200, 201), created.text
    refused = engines.post("/obligations", {
        "kind": "PAYMENT", "title": "Not ours", "rule": {"kind": "FIXED", "date": "2026-12-15"},
        "owner_user_id": str(engines.tenant.other_org_member.user.id),
    })
    assert refused.status_code in (409, 422), refused.text


def test_an_obligation_owner_who_loses_access_is_cleared(engines: Engines) -> None:
    engines.plan("business")
    contributor = engines.tenant.contributor.user
    created = engines.post("/obligations", {
        "kind": "PAYMENT", "title": "Rent", "rule": {"kind": "FIXED", "date": "2026-12-15"},
        "owner_user_id": str(contributor.id),
    })
    assert created.status_code in (200, 201), created.text
    body = created.json()
    obligation_id = (body.get("obligation") or body)["id"]
    grants = {row["user"]["email"]: row["id"] for row in engines.get("/members").json()["items"] if row["id"]}
    assert engines.post(f"/members/{grants[contributor.email]}/revoke").status_code == 200
    after = engines.get(f"/obligations/{obligation_id}").json()
    assert (after.get("obligation") or after).get("owner_user_id") in (None, ""), after
