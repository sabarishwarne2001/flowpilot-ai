"""The workspace overview's numbers (GET /workspaces/{id}/dashboard/overview).

F-161: a workspace where nothing has finished processing showed "Success rate 100%" (no document had
       succeeded, none had failed). The rate is now absent until a document finishes, and the page
       shows a dash.

Phase 1: the overview's "document types" were file formats (PDF, IMAGE/PNG). It now also says what
         the documents are, from the classifier's label (Invoice, Purchase Order, ...).
"""

from __future__ import annotations


def _overview(client, tenant) -> dict:
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/dashboard/overview", headers=tenant.owner.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_empty_workspace_has_no_success_rate(client, tenant):
    body = _overview(client, tenant)

    assert body["total_work_items"] == 0
    assert body["automation_success_rate"] is None


def test_documents_still_processing_have_no_success_rate(client, db_session, tenant, work_item_factory):
    work_item_factory(original_filename="queued.pdf").status = "QUEUED"
    db_session.commit()

    assert _overview(client, tenant)["automation_success_rate"] is None


def test_the_rate_counts_finished_documents(client, db_session, tenant, work_item_factory):
    for name, status in [("a.pdf", "COMPLETED"), ("b.pdf", "COMPLETED"), ("c.pdf", "COMPLETED"), ("d.pdf", "FAILED")]:
        work_item_factory(original_filename=name).status = status
    db_session.commit()

    body = _overview(client, tenant)

    assert body["automation_success_rate"] == 75.0
    assert body["failed_count"] == 1


def test_document_kinds_come_from_the_classifier(client, db_session, tenant, work_item_factory):
    work_item_factory(original_filename="a.pdf", classification="Invoice").status = "COMPLETED"
    work_item_factory(original_filename="b.pdf", classification="Invoice").status = "COMPLETED"
    work_item_factory(
        original_filename="c.pdf",
        extracted_entities={"classification_details": {"document_classification": "Purchase Order"}},
    ).status = "COMPLETED"
    work_item_factory(original_filename="queued.pdf").status = "QUEUED"  # not classified yet
    db_session.commit()

    body = _overview(client, tenant)

    assert body["classification_distribution"] == [
        {"document_type": "Invoice", "count": 2, "percentage": 66.7},
        {"document_type": "Purchase Order", "count": 1, "percentage": 33.3},
    ]
    # The file formats are still there, over every document.
    assert body["document_type_distribution"] == [{"document_type": "PDF", "count": 4, "percentage": 100.0}]


def test_an_empty_workspace_has_no_document_kinds(client, tenant):
    assert _overview(client, tenant)["classification_distribution"] == []
