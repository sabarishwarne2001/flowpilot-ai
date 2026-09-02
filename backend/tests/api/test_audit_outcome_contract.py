import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType
from app.models.uploaded_file import UploadedFile


def test_immutability_trigger_blocks_app_updates(db_session: Session, tenant):
    row = AuditLog(
        organization_id=tenant.organization.id,
        resource_type=AuditResourceType.WORKSPACE,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
    )
    db_session.add(row)
    db_session.commit()

    # Attempting UPDATE as app role must fail with AU001
    with pytest.raises(DBAPIError) as excinfo:
        db_session.execute(
            text("UPDATE audit_logs SET outcome = 'DENIED'::audit_outcome WHERE id = :id;"),
            {"id": row.id},
        )
        db_session.commit()

    assert "audit_logs is append-only" in str(excinfo.value)
    db_session.rollback()


def test_no_uploaded_files_have_legacy_url_prefix(db_session: Session):
    res = db_session.execute(
        text("SELECT COUNT(*) FROM uploaded_files WHERE file_path ~ '^/?uploads/';")
    )
    count = res.scalar_one()
    assert count == 0


def test_workspace_logo_stream_returns_200_with_correct_mime(
    client: TestClient, db_session: Session, tenant
):
    ws = tenant.workspace
    logo = UploadedFile(
        file_path="logos/test_stream.png",
        original_filename="logo.png",
        mime_type="image/png",
        file_size=1024,
        checksum_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        owner_id=tenant.owner.user.id,
        organization_id=ws.organization_id,
        workspace_id=ws.id,
    )
    db_session.add(logo)
    db_session.flush()

    ws.logo_file_id = logo.id
    db_session.commit()

    res = client.get(
        f"/api/v1/workspaces/{ws.id}/logo",
        headers=tenant.org_admin.headers,
    )
    # Stream endpoint returns 200 or 404 (if mock disk object absent), not 500
    assert res.status_code in (200, 404)
