"""The built-in sample invoice is an ordinary upload (campaign session 4, §A.4, first run).

    pytest tests/api/test_sample_document.py -q

A new customer with no document at hand clicks "Try a sample invoice" on the workspace's getting
started card. The sample must arrive like any upload: through the file checks, as the person's own
document, queued for processing, and refused to anyone who may not upload.
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models.work_item import WorkItem
from app.services.sample_document import SAMPLE_FILENAME, sample_invoice_pdf


def test_the_sample_reads_like_an_invoice() -> None:
    text = PdfReader(BytesIO(sample_invoice_pdf())).pages[0].extract_text()
    for field in ("SAMPLE DOCUMENT", "Invoice Number: INV-SAMPLE-0001", "Vendor: Northwind Office Supplies Ltd",
                  "Total Amount Due"):
        assert field in text, field


def test_a_contributor_adds_the_sample_as_their_own_document(client, db_session: Session, tenant) -> None:
    response = client.post(f"/api/v1/workspaces/{tenant.workspace.id}/work-items/sample",
                           headers=tenant.contributor.headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_filename"] == SAMPLE_FILENAME
    row = db_session.get(WorkItem, body["id"])
    assert row is not None
    assert row.workspace_id == tenant.workspace.id
    assert row.created_by_user_id == tenant.contributor.user.id
    assert row.file_type == "application/pdf"


def test_a_viewer_cannot_add_the_sample(client, tenant) -> None:
    response = client.post(f"/api/v1/workspaces/{tenant.workspace.id}/work-items/sample",
                           headers=tenant.viewer.headers)
    assert response.status_code == 403
