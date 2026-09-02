from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models.uploaded_file import UploadedFile


def test_me_context_and_workspaces_return_logo_route(
    client: TestClient,
    db_session: Session,
    tenant,
):
    user = tenant.owner.user
    test_workspace = tenant.workspace

    logo = UploadedFile(
        file_path="logos/test.png",
        original_filename="logo.png",
        mime_type="image/png",
        file_size=1024,
        checksum_sha256="dummychecksum",
        owner_id=user.id,
        organization_id=test_workspace.organization_id,
        workspace_id=test_workspace.id,
    )
    db_session.add(logo)
    db_session.flush()

    test_workspace.logo_file_id = logo.id
    db_session.commit()

    token = create_access_token(subject=str(user.id))
    headers = {"Authorization": f"Bearer {token}"}

    res_context = client.get("/api/v1/me/context", headers=headers)
    assert res_context.status_code == 200
    data_context = res_context.json()
    org = data_context["organizations"][0]
    ws = org["workspaces"][0]
    assert ws["company_logo_url"] == f"/api/v1/workspaces/{test_workspace.id}/logo"

    res_ws = client.get("/api/v1/me/workspaces", headers=headers)
    assert res_ws.status_code == 200
    workspaces = res_ws.json()
    assert len(workspaces) > 0
    assert workspaces[0]["company_logo_url"] == f"/api/v1/workspaces/{test_workspace.id}/logo"
