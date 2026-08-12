"""
ARCH-06 Step 1b regression suite — A.2.1 / exit criterion E10.

    "No user can delete or read another tenant's uploaded file."

THIS SUITE MUST BE OBSERVED FAILING AGAINST THE PRE-1B CODE.
------------------------------------------------------------
That instruction is not ceremony. Against the old code the routes lived at
`/upload/logo` with no workspace segment, so every request here 404s on
ROUTING rather than on authorization — and a suite that goes green because the
URL does not exist proves nothing about who may delete what. To observe the
real failure, point `_url()` at the old flat path and run
`test_foreign_tenant_admin_cannot_delete_by_naming_url`: the old handler
unlinks the file and returns 200, and the assertion on
`logo_file.exists()` is what fails. That is the vulnerability, reproduced.

Uses the shared `tenant` fixture from tests/conftest.py — two organizations
and seven personas — because the persona that matters here is
`other_org_member`: an ACTIVE, entirely legitimate user of the platform who
holds ADMIN on their own workspace and simply belongs somewhere else. They are
the persona that catches an authorization check written as "is this caller
authenticated" or even "is this caller an admin somewhere" rather than "is
this caller an admin of THIS workspace, and is this file THIS workspace's".

Real HTTP through `client`, real guards, no mocking of deps or the service
layer. Overriding the guard would test a mock of the boundary instead of the
boundary, and the boundary is the entire subject.

FOUR GROUPS
-----------
  1. Cross-tenant deletion — the vulnerability itself, from every angle a
     caller can reach it.
  2. Role enforcement inside the legitimate tenant — 403 for members below
     ADMIN, including derived organization elevation.
  3. The happy path — because a fix that also breaks legitimate deletion is
     not a fix.
  4. Upload symmetry — POST is gated identically, so the write side does not
     become the new soft spot.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.upload import UPLOAD_DIR
from app.models.workspace import Workspace


# ===========================================================================
# Helpers
# ===========================================================================

def _url(workspace_id) -> str:
    """The workspace-scoped logo route."""
    return f"/api/v1/workspaces/{workspace_id}/upload/logo"


#: A one-pixel PNG. Real bytes, so this suite keeps passing unchanged when
#: Step 7 adds magic-byte validation to the upload path.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


def _write_logo(db: Session, workspace: Workspace) -> Path:
    """
    Puts a real file on disk and attaches it to the workspace.

    Both halves matter. The file proves an unlink did or did not happen; the
    `company_logo_url` pointer is the ownership record the fix checks against
    (Step 1b stands in for the `uploaded_files` table until Step 5).
    """
    filename = f"{uuid.uuid4()}.png"
    path = UPLOAD_DIR / filename
    path.write_bytes(_PNG_BYTES)

    workspace.company_logo_url = f"/uploads/logos/{filename}"
    db.add(workspace)
    db.commit()

    return path


@pytest.fixture()
def victim_logo(db_session: Session, tenant) -> Generator[Path, None, None]:
    """The target workspace's logo: a real file with a real ownership record."""
    path = _write_logo(db_session, tenant.workspace)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture()
def attacker_logo(db_session: Session, tenant) -> Generator[Path, None, None]:
    """
    A logo belonging to the OTHER organization's workspace.

    Exists so the attacker's own request is well-formed in every respect
    except the one under test — they are a genuine ADMIN of a workspace that
    genuinely has a logo, and the only thing wrong is which file they name.
    """
    path = _write_logo(db_session, tenant.foreign_workspace)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _delete(client: TestClient, workspace_id, logo_url: str, headers: dict):
    """
    DELETE with a body.

    Sent via `client.request` rather than `client.delete`, because httpx's
    `delete()` shorthand does not accept a `json=` argument.
    """
    return client.request(
        "DELETE",
        _url(workspace_id),
        json={"logo_url": logo_url},
        headers=headers,
    )


# ===========================================================================
# 1. Cross-tenant deletion — the vulnerability
# ===========================================================================

class TestCrossTenantDeletion:

    def test_foreign_tenant_admin_cannot_delete_by_naming_workspace(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        """
        The direct attack: name the victim's workspace and the victim's logo.

        404 rather than 403. A 403 would confirm the workspace exists, which
        is an enumeration oracle — the same rule
        `WorkspaceAccessDeniedError` already applies everywhere else.
        """
        response = _delete(
            client,
            tenant.workspace.id,
            tenant.workspace.company_logo_url,
            tenant.other_org_member.headers,
        )

        assert response.status_code == 404
        assert victim_logo.exists(), "Another tenant's file was unlinked."

    def test_foreign_tenant_admin_cannot_delete_by_naming_url(
        self, client: TestClient, db_session: Session, tenant,
        victim_logo: Path, attacker_logo: Path,
    ) -> None:
        """
        THE LOAD-BEARING TEST IN THIS MODULE.

        The attacker passes their OWN workspace_id — where they are a
        legitimate ADMIN, so RequireWorkspaceAdmin passes cleanly — and the
        VICTIM's logo_url. Every check that asks "may this caller act on this
        workspace" answers yes. Only the ownership comparison between the
        submitted URL and this workspace's stored `company_logo_url` stops it.

        Delete that comparison and this is the one test that fails.
        """
        response = _delete(
            client,
            tenant.foreign_workspace.id,
            tenant.workspace.company_logo_url,
            tenant.other_org_member.headers,
        )

        assert response.status_code == 404
        assert victim_logo.exists(), "Another tenant's file was unlinked."
        assert attacker_logo.exists(), "The caller's own logo was collateral."

        db_session.refresh(tenant.workspace)
        assert tenant.workspace.company_logo_url is not None, (
            "The victim's pointer was cleared by a foreign caller."
        )

    def test_non_member_cannot_delete(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        """An authenticated account belonging to no organization at all."""
        response = _delete(
            client,
            tenant.workspace.id,
            tenant.workspace.company_logo_url,
            tenant.non_member.headers,
        )

        assert response.status_code == 404
        assert victim_logo.exists()

    def test_unauthenticated_cannot_delete(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        response = client.request(
            "DELETE",
            _url(tenant.workspace.id),
            json={"logo_url": tenant.workspace.company_logo_url},
        )

        assert response.status_code == 401
        assert victim_logo.exists()

    def test_path_traversal_is_rejected(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        """
        ARCH-01 PF-2, still closed.

        Traversal is unreachable here because the submitted URL must equal a
        value this service generated, and no traversal string ever will. The
        test asserts the OUTCOME rather than the mechanism, so it keeps
        holding if the ownership check is ever re-implemented differently.
        """
        for hostile in (
            "/uploads/logos/../../etc/passwd",
            "/uploads/logos/../../../app/main.py",
            "../../etc/passwd",
            "/etc/passwd",
        ):
            response = _delete(
                client, tenant.workspace.id, hostile, tenant.ws_admin.headers
            )
            assert response.status_code == 404, hostile
            assert victim_logo.exists()

    def test_deleting_a_stale_url_does_not_clear_the_current_logo(
        self, client: TestClient, db_session: Session, tenant, victim_logo: Path
    ) -> None:
        """
        Compare-and-delete, not blind delete.

        A browser tab holding a logo URL that has since been replaced must
        404, not remove whatever the workspace points at now. This is why
        DeleteLogoRequest still carries a URL the server could have looked up
        itself.
        """
        stale = "/uploads/logos/00000000-0000-4000-8000-000000000000.png"

        response = _delete(
            client, tenant.workspace.id, stale, tenant.ws_admin.headers
        )

        assert response.status_code == 404
        assert victim_logo.exists()

        db_session.refresh(tenant.workspace)
        assert tenant.workspace.company_logo_url is not None


# ===========================================================================
# 2. Role enforcement inside the legitimate tenant
# ===========================================================================

class TestRoleEnforcement:

    @pytest.mark.parametrize("persona_name", ["contributor", "viewer"])
    def test_below_admin_is_forbidden(
        self, client: TestClient, tenant, victim_logo: Path, persona_name: str
    ) -> None:
        """
        403, not 404, and the distinction is deliberate.

        These callers already hold access to the workspace, so its existence
        is not a secret from them. Returning 404 here would send a legitimate
        member hunting for a workspace that is right in front of them.
        """
        persona = getattr(tenant, persona_name)

        response = _delete(
            client,
            tenant.workspace.id,
            tenant.workspace.company_logo_url,
            persona.headers,
        )

        assert response.status_code == 403
        assert victim_logo.exists()

    def test_organization_admin_may_delete_without_a_stored_grant(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        """
        Derived elevation, not a stored row.

        `org_admin` deliberately holds NO WorkspaceMember grant. An
        authorization check written against stored memberships would deny the
        most privileged account in the tenant — the defect that hid every
        settings control before ARCH-01. The effective role must resolve to
        ADMIN here.
        """
        response = _delete(
            client,
            tenant.workspace.id,
            tenant.workspace.company_logo_url,
            tenant.org_admin.headers,
        )

        assert response.status_code == 200
        assert not victim_logo.exists()


# ===========================================================================
# 3. The happy path
# ===========================================================================

class TestLegitimateDeletion:

    def test_workspace_admin_deletes_file_and_clears_pointer(
        self, client: TestClient, db_session: Session, tenant, victim_logo: Path
    ) -> None:
        """
        Both stores, not one.

        The pre-existing DELETE /workspaces/{id}/logo clears the pointer and
        leaves the file behind — that is A.2.3's orphan accumulation. This
        route must do both.
        """
        response = _delete(
            client,
            tenant.workspace.id,
            tenant.workspace.company_logo_url,
            tenant.ws_admin.headers,
        )

        assert response.status_code == 200
        assert response.json()["company_logo_url"] is None
        assert not victim_logo.exists(), "The file was left on disk."

        db_session.refresh(tenant.workspace)
        assert tenant.workspace.company_logo_url is None

    def test_second_delete_is_a_clean_404(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        """
        Replaying a successful delete must 404, never 500.

        After the first call the pointer is NULL, so the ownership check finds
        nothing to match — which is the same code path as "this workspace has
        no logo", and correctly indistinguishable from it.
        """
        stored = tenant.workspace.company_logo_url

        first = _delete(client, tenant.workspace.id, stored, tenant.ws_admin.headers)
        assert first.status_code == 200

        second = _delete(client, tenant.workspace.id, stored, tenant.ws_admin.headers)
        assert second.status_code == 404

    def test_unknown_workspace_is_404(
        self, client: TestClient, tenant, victim_logo: Path
    ) -> None:
        response = _delete(
            client,
            uuid.uuid4(),
            tenant.workspace.company_logo_url,
            tenant.ws_admin.headers,
        )

        assert response.status_code == 404
        assert victim_logo.exists()


# ===========================================================================
# 4. Upload symmetry
# ===========================================================================

class TestUploadAuthorization:
    """
    The write side is gated identically.

    Fixing only DELETE would leave POST as a route that writes bytes to disk
    in a tenant's name for any authenticated caller — a quota and storage
    surface rather than a data-destruction one, but the same missing check.
    """

    @staticmethod
    def _post(client: TestClient, workspace_id, headers: dict):
        return client.post(
            _url(workspace_id),
            files={"file": ("logo.png", _PNG_BYTES, "image/png")},
            headers=headers,
        )

    def test_foreign_tenant_member_cannot_upload(
        self, client: TestClient, tenant
    ) -> None:
        response = self._post(
            client, tenant.workspace.id, tenant.other_org_member.headers
        )
        assert response.status_code == 404

    def test_viewer_cannot_upload(self, client: TestClient, tenant) -> None:
        response = self._post(
            client, tenant.workspace.id, tenant.viewer.headers
        )
        assert response.status_code == 403

    def test_unauthenticated_cannot_upload(
        self, client: TestClient, tenant
    ) -> None:
        response = client.post(
            _url(tenant.workspace.id),
            files={"file": ("logo.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 401

    def test_workspace_admin_uploads_successfully(
        self, client: TestClient, tenant
    ) -> None:
        response = self._post(
            client, tenant.workspace.id, tenant.ws_admin.headers
        )

        assert response.status_code == 200
        logo_url = response.json()["logo_url"]
        assert logo_url.startswith("/uploads/logos/")

        written = UPLOAD_DIR / Path(logo_url).name
        try:
            assert written.exists()
        finally:
            written.unlink(missing_ok=True)

    def test_disallowed_content_type_is_rejected(
        self, client: TestClient, tenant
    ) -> None:
        """
        The Content-Type check is unchanged by Step 1 and is asserted here so
        the behaviour is pinned before Step 7 replaces it.

        NOTE: this passes on a HEADER, not on bytes. A.2.2 — an HTML payload
        sent as `image/png` is still accepted today. Step 7 adds the
        magic-byte test (E9); it is not in scope here and this assertion must
        not be read as covering it.
        """
        response = client.post(
            _url(tenant.workspace.id),
            files={"file": ("evil.svg", b"<svg/>", "image/svg+xml")},
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 400