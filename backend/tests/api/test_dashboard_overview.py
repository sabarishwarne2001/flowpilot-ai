"""The workspace overview's numbers (GET /workspaces/{id}/dashboard/overview).

F-161: a workspace where nothing has finished processing showed "Success rate 100%" (no document had
       succeeded, none had failed). The rate is now absent until a document finishes, and the page
       shows a dash.
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
