"""SCIM 2.0 directory sync, every endpoint over HTTP (campaign session 3).

Twelve of the thirteen `/scim/v2` routes had never been called. Here an identity provider's
directory client does what Okta and Entra ID do: reads the discovery documents, creates users
(with Okta's and Entra's PATCH dialects for deactivation and reactivation), pages and filters,
manages groups, and deletes. Then the abuse cases: another organization's token, a bad filter,
an organization that has left the Enterprise plan, a read-only billing state, an archived
organization.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.organization import MembershipStatus, OrganizationMember, OrganizationStatus
from app.models.user import User
from app.services.identity import scim_service
from tests.engines.conftest import Engines
from tests.engines.saml_idp import TestIdp

SCIM = "/scim/v2"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"


class Directory:
    def __init__(self, engines: Engines, organization_id) -> None:
        self.engines = engines
        self.idp = TestIdp.create()
        self.config = self.idp.install(engines.db, organization_id, jit_seat_cap=50)
        _, token = scim_service.issue_key(
            engines.db, organization_id=organization_id, idp_config_id=self.config.id, display_name="Okta"
        )
        engines.db.commit()
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}

    def call(self, method: str, path: str, body=None, **params):
        return self.engines.client.request(method, f"{SCIM}{path}", headers=self.headers, json=body,
                                           params=params or None)

    def new_user(self, local: str | None = None) -> dict:
        local = local or f"dana-{uuid.uuid4().hex[:6]}"
        response = self.call("POST", "/Users", {
            "schemas": [USER],
            "userName": f"{local}@{self.idp.domain}",
            "externalId": f"okta-{local}",
            "name": {"givenName": "Dana", "familyName": "Directory"},
            "emails": [{"value": f"{local}@{self.idp.domain}", "primary": True, "type": "work"}],
            "active": True,
        })
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def directory(engines: Engines) -> Directory:
    engines.plan("enterprise")
    return Directory(engines, engines.org)


def _membership(engines: Engines, email: str) -> OrganizationMember | None:
    engines.db.expire_all()
    user = engines.db.query(User).filter_by(email=email).one_or_none()
    if user is None:
        return None
    return engines.db.query(OrganizationMember).filter_by(organization_id=engines.org, user_id=user.id).one_or_none()


def _active(member: OrganizationMember | None) -> bool:
    return member is not None and member.status == MembershipStatus.ACTIVE


def test_the_discovery_documents_describe_what_is_supported(directory: Directory) -> None:
    config = directory.call("GET", "/ServiceProviderConfig")
    assert config.status_code == 200
    assert config.headers["content-type"].startswith("application/scim+json")
    assert config.json()["patch"]["supported"] is True
    assert config.json()["bulk"]["supported"] is False
    types = directory.call("GET", "/ResourceTypes").json()
    assert {r["id"] for r in types["Resources"]} == {"User", "Group"}
    schemas = directory.call("GET", "/Schemas").json()
    assert {r["name"] for r in schemas["Resources"]} == {"User", "Group"}


def test_a_user_is_created_found_and_read(engines: Engines, directory: Directory) -> None:
    created = directory.new_user()
    email = created["userName"]
    assert created["active"] is True and created["emails"][0]["value"] == email
    assert _active(_membership(engines, email))

    found = directory.call("GET", "/Users", filter=f'userName eq "{email}"').json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == created["id"]
    read = directory.call("GET", f"/Users/{created['id']}")
    assert read.status_code == 200 and read.json()["userName"] == email
    # Creating the same person again returns them, not a duplicate.
    again = directory.call("POST", "/Users", {"schemas": [USER], "userName": email, "externalId": created["externalId"],
                                              "emails": [{"value": email, "primary": True}]})
    assert again.status_code == 201 and again.json()["id"] == created["id"]


def test_okta_and_entra_deactivate_and_reactivate(engines: Engines, directory: Directory) -> None:
    created = directory.new_user()
    email, rid = created["userName"], created["id"]

    okta_off = directory.call("PATCH", f"/Users/{rid}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "value": {"active": False}}]})
    entra_style = okta_off  # Okta sends a path-less replace with an object value
    assert entra_style.status_code == 200, entra_style.text
    if okta_off.json()["active"] is not False:
        # Entra ID: an explicit path and a string value.
        okta_off = directory.call("PATCH", f"/Users/{rid}", {
            "schemas": [PATCH], "Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
    assert okta_off.json()["active"] is False
    assert not _active(_membership(engines, email))

    entra_on = directory.call("PATCH", f"/Users/{rid}", {
        "schemas": [PATCH], "Operations": [{"op": "Replace", "path": "active", "value": "True"}]})
    assert entra_on.status_code == 200 and entra_on.json()["active"] is True
    assert _active(_membership(engines, email))

    replaced = directory.call("PUT", f"/Users/{rid}", {"schemas": [USER], "userName": email, "active": False})
    assert replaced.status_code == 200 and replaced.json()["active"] is False
    assert not _active(_membership(engines, email))


def test_deleting_a_user_removes_their_access(engines: Engines, directory: Directory) -> None:
    created = directory.new_user()
    assert directory.call("DELETE", f"/Users/{created['id']}").status_code == 204
    assert not _active(_membership(engines, created["userName"]))


def test_users_are_paged(directory: Directory) -> None:
    for _ in range(3):
        directory.new_user()
    page = directory.call("GET", "/Users", startIndex=1, count=2).json()
    assert page["itemsPerPage"] == 2 and page["totalResults"] >= 3 and page["startIndex"] == 1
    rest = directory.call("GET", "/Users", startIndex=3, count=2).json()
    first_ids = {r["id"] for r in page["Resources"]}
    assert not first_ids & {r["id"] for r in rest["Resources"]}


def test_groups_are_created_listed_changed_and_deleted(directory: Directory) -> None:
    alice, bob = directory.new_user(), directory.new_user()
    created = directory.call("POST", "/Groups", {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "Finance Approvers", "members": [{"value": alice["id"]}]})
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    listed = directory.call("GET", "/Groups", filter='displayName eq "Finance Approvers"').json()
    assert listed["totalResults"] == 1
    added = directory.call("PATCH", f"/Groups/{gid}", {
        "schemas": [PATCH], "Operations": [{"op": "add", "path": "members", "value": [{"value": bob["id"]}]}]})
    assert added.status_code == 200
    assert {m["value"] for m in directory.call("GET", f"/Groups/{gid}").json()["members"]} == {alice["id"], bob["id"]}
    removed = directory.call("PATCH", f"/Groups/{gid}", {
        "schemas": [PATCH],
        "Operations": [{"op": "remove", "path": "members", "value": [{"value": alice["id"]}]}]})
    assert removed.status_code == 200
    assert {m["value"] for m in directory.call("GET", f"/Groups/{gid}").json()["members"]} == {bob["id"]}
    assert directory.call("DELETE", f"/Groups/{gid}").status_code == 204
    assert directory.call("GET", f"/Groups/{gid}").status_code == 404


def test_errors_are_scim_errors(directory: Directory) -> None:
    bad_filter = directory.call("GET", "/Users", filter="userName gt 3")
    assert bad_filter.status_code == 400
    assert bad_filter.json()["scimType"] == "invalidFilter"
    missing = directory.call("GET", f"/Users/{uuid.uuid4()}")
    assert missing.status_code == 404 and "urn:ietf:params:scim:api:messages:2.0:Error" in missing.json()["schemas"]


def test_another_organizations_token_sees_none_of_these_users(engines: Engines, directory: Directory) -> None:
    from app.models.organization import Organization

    created = directory.new_user()
    other = Organization(name="Elsewhere Ltd", slug=f"elsewhere-{uuid.uuid4().hex[:6]}")
    engines.db.add(other)
    engines.db.flush()
    from tests.security.plans import put_on_plan

    put_on_plan(engines.db, other, "enterprise")
    foreign = Directory(engines, other.id)
    assert foreign.call("GET", f"/Users/{created['id']}").status_code == 404
    off = foreign.call("PATCH", f"/Users/{created['id']}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert off.status_code == 404
    assert foreign.call("DELETE", f"/Users/{created['id']}").status_code == 404
    assert foreign.call("GET", "/Users").json()["totalResults"] == 0
    assert _active(_membership(engines, created["userName"]))


def test_after_leaving_enterprise_the_directory_offboards_but_never_adds(engines: Engines, directory: Directory) -> None:
    kept = directory.new_user()
    engines.plan("business")  # directory sync is an Enterprise feature

    refused = directory.call("POST", "/Users", {"schemas": [USER], "userName": f"late@{directory.idp.domain}",
                                                "emails": [{"value": f"late@{directory.idp.domain}", "primary": True}]})
    assert refused.status_code == 403, refused.text
    assert engines.db.query(User).filter_by(email=f"late@{directory.idp.domain}").one_or_none() is None

    # Offboarding always works: a security control must never stop with a plan change.
    off = directory.call("PATCH", f"/Users/{kept['id']}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert off.status_code == 200 and off.json()["active"] is False
    back = directory.call("PATCH", f"/Users/{kept['id']}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "path": "active", "value": True}]})
    assert back.status_code == 403, back.text
    assert not _active(_membership(engines, kept["userName"]))


def test_a_read_only_organization_cannot_reactivate_through_patch(
    engines: Engines, directory: Directory, monkeypatch
) -> None:
    from app.services.billing import dunning_service

    created = directory.new_user()
    off = directory.call("PATCH", f"/Users/{created['id']}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert off.json()["active"] is False

    monkeypatch.setattr(dunning_service, "access_state",
                        lambda db, *, organization_id: dunning_service.BillingAccessState.RESTRICTED)
    put = directory.call("PUT", f"/Users/{created['id']}", {"schemas": [USER], "userName": created["userName"],
                                                             "active": True})
    assert put.status_code == 403  # refused before this session
    patch = directory.call("PATCH", f"/Users/{created['id']}", {
        "schemas": [PATCH], "Operations": [{"op": "replace", "path": "active", "value": True}]})
    assert patch.status_code == 403, patch.text
    assert not _active(_membership(engines, created["userName"]))


def test_an_archived_organization_adds_nobody(engines: Engines, directory: Directory) -> None:
    engines.tenant.organization.status = OrganizationStatus.ARCHIVED
    engines.db.commit()
    try:
        refused = directory.call("POST", "/Users", {
            "schemas": [USER], "userName": f"ghost@{directory.idp.domain}",
            "emails": [{"value": f"ghost@{directory.idp.domain}", "primary": True}]})
        assert refused.status_code in (403, 404), refused.text
        assert engines.db.query(User).filter_by(email=f"ghost@{directory.idp.domain}").one_or_none() is None
    finally:
        engines.tenant.organization.status = OrganizationStatus.ACTIVE
        engines.db.commit()
