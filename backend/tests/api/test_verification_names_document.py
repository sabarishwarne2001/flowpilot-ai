"""F-176: the extraction review workbench named each document by the first eight characters of its id.

The verification list and detail carried only `work_item_id`, so the workbench showed "d48d4635" and
"Document d48d4635" where a reviewer needs the file name. Both now carry `original_filename`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.verification import DocumentVerification, VerificationStatus

pytestmark = pytest.mark.usefixtures("test_database")


def test_list_and_detail_carry_the_file_name(client: TestClient, db_session: Session, tenant, work_item_factory) -> None:
    work_item = work_item_factory()
    work_item.original_filename = "invoice-INV-F176.pdf"
    verification = DocumentVerification(
        work_item_id=work_item.id, workspace_id=tenant.workspace.id, organization_id=tenant.organization.id,
        status=VerificationStatus.PENDING, agent_count=2,
    )
    db_session.add(verification)
    db_session.commit()

    base = f"/api/v1/workspaces/{tenant.workspace.id}/verifications"
    headers = {"Authorization": f"Bearer {tenant.owner.token}"}
    listed = client.get(base, headers=headers)
    assert listed.status_code == 200, listed.text
    [row] = [r for r in listed.json() if r["id"] == str(verification.id)]
    assert row["original_filename"] == "invoice-INV-F176.pdf"

    detail = client.get(f"{base}/{verification.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["original_filename"] == "invoice-INV-F176.pdf"
