"""
ARCH-06 Step 1b / ARCH-08 Step 1 upload authorization test suite.
Tests logo upload and deletion security gates.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.uploaded_file import UploadedFile
from app.models.workspace import Workspace

UPLOAD_DIR = settings.UPLOAD_DIR


def _url(workspace_id) -> str:
    """The workspace-scoped logo route."""
    return f"/api/v1/workspaces/{workspace_id}/logo"


_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


def _write_logo(db: Session, workspace: Workspace) -> UploadedFile:
    filename = f"logos/{uuid.uuid4().hex}.png"
    record = UploadedFile(
        file_path=filename,
        original_filename="logo.png",
        mime_type="image/png",
        file_size=len(_PNG_BYTES),
        checksum_sha256="dummychecksum",
        owner_id=workspace.organization.owner_id,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )
    db.add(record)
    db.flush()

    workspace.logo_file_id = record.id
    db.add(workspace)
    db.commit()

    return record


class TestCrossTenantDeletion:

    def test_foreign_tenant_admin_cannot_delete_by_naming_workspace(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        logo = _write_logo(db_session, tenant.workspace)

        response = client.delete(
            _url(tenant.workspace.id),
            headers=tenant.other_org_member.headers,
        )

        assert response.status_code == 404
        db_session.refresh(logo)
        assert logo.deleted_at is None

    def test_foreign_tenant_admin_cannot_delete_victim_logo(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        victim_logo = _write_logo(db_session, tenant.workspace)
        attacker_logo = _write_logo(db_session, tenant.foreign_workspace)

        response = client.delete(
            _url(tenant.foreign_workspace.id),
            headers=tenant.other_org_member.headers,
        )

        assert response.status_code == 200
        db_session.refresh(victim_logo)
        db_session.refresh(attacker_logo)
        assert victim_logo.deleted_at is None
        assert attacker_logo.deleted_at is not None

    def test_unauthenticated_cannot_delete(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        logo = _write_logo(db_session, tenant.workspace)

        response = client.delete(_url(tenant.workspace.id))

        assert response.status_code == 401
        db_session.refresh(logo)
        assert logo.deleted_at is None


class TestRoleEnforcement:

    @pytest.mark.parametrize("persona_name", ["contributor", "viewer"])
    def test_below_admin_is_forbidden(
        self, client: TestClient, db_session: Session, tenant, persona_name: str
    ) -> None:
        logo = _write_logo(db_session, tenant.workspace)
        persona = getattr(tenant, persona_name)

        response = client.delete(
            _url(tenant.workspace.id),
            headers=persona.headers,
        )

        assert response.status_code == 403
        db_session.refresh(logo)
        assert logo.deleted_at is None

    def test_organization_admin_may_delete_without_a_stored_grant(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        logo = _write_logo(db_session, tenant.workspace)

        response = client.delete(
            _url(tenant.workspace.id),
            headers=tenant.org_admin.headers,
        )

        assert response.status_code == 200
        db_session.refresh(logo)
        assert logo.deleted_at is not None


class TestLegitimateDeletion:

    def test_workspace_admin_deletes_file_and_clears_pointer(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        logo = _write_logo(db_session, tenant.workspace)

        response = client.delete(
            _url(tenant.workspace.id),
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 200
        assert response.json()["company_logo_url"] is None

        db_session.refresh(tenant.workspace)
        db_session.refresh(logo)
        assert tenant.workspace.logo_file_id is None
        assert logo.deleted_at is not None


class TestUploadAuthorization:

    @staticmethod
    def _post(client: TestClient, workspace_id, headers: dict):
        return client.post(
            "/api/v1/logo",
            files={"file": ("logo.png", _PNG_BYTES, "image/png")},
            headers=headers,
        )

    def test_unauthenticated_cannot_upload(
        self, client: TestClient, tenant
    ) -> None:
        response = client.post(
            "/api/v1/logo",
            files={"file": ("logo.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 401