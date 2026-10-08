"""Phase 1 live findings on the Documents list (GET /workspaces/{id}/work-items).

F-155: totalItems and totalPages ignored the search and status filters, so a filtered list said
       "Page 1 of 5" over three matching documents and the later pages were empty.
F-156: the search was a raw LIKE pattern, so "_" and "%" matched every document and a filename
       containing "_" also matched names with any character in that place.
F-157: rows that share the sort value (same size, same name) had no tie-breaker, so paging could
       show a document twice and skip another.
"""

from __future__ import annotations

from app.models.work_item import WorkItem


def _list(client, tenant, **params):
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        params=params,
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed(db_session, work_item_factory, names_and_statuses):
    items = []
    for name, status in names_and_statuses:
        item = work_item_factory(original_filename=name)
        item.status = status
        items.append(item)
    db_session.commit()
    return items


def test_totals_follow_the_status_filter(client, db_session, tenant, work_item_factory):
    _seed(
        db_session,
        work_item_factory,
        [(f"ok-{i}.pdf", "COMPLETED") for i in range(5)] + [("broken.pdf", "FAILED")],
    )

    body = _list(client, tenant, status="FAILED", limit=2)

    assert [row["original_filename"] for row in body["items"]] == ["broken.pdf"]
    assert body["totalItems"] == 1
    assert body["totalPages"] == 1


def test_totals_follow_the_search(client, db_session, tenant, work_item_factory):
    _seed(
        db_session,
        work_item_factory,
        [("invoice-1.pdf", "COMPLETED"), ("invoice-2.pdf", "COMPLETED"), ("contract.pdf", "COMPLETED")],
    )

    body = _list(client, tenant, search="invoice", limit=1)

    assert body["totalItems"] == 2
    assert body["totalPages"] == 2


def test_search_treats_underscore_and_percent_as_text(client, db_session, tenant, work_item_factory):
    _seed(
        db_session,
        work_item_factory,
        [
            ("Procurement_Policy.pdf", "COMPLETED"),
            ("ProcurementXPolicy.pdf", "COMPLETED"),
            ("discount 50%.pdf", "COMPLETED"),
            ("plain.pdf", "COMPLETED"),
        ],
    )

    underscore = _list(client, tenant, search="_")
    assert [row["original_filename"] for row in underscore["items"]] == ["Procurement_Policy.pdf"]
    assert underscore["totalItems"] == 1

    named = _list(client, tenant, search="Procurement_Policy")
    assert [row["original_filename"] for row in named["items"]] == ["Procurement_Policy.pdf"]

    percent = _list(client, tenant, search="%")
    assert [row["original_filename"] for row in percent["items"]] == ["discount 50%.pdf"]

    backslash = _list(client, tenant, search="\\")
    assert backslash["items"] == [] and backslash["totalItems"] == 0


def test_paging_over_equal_sort_values_shows_every_document_once(
    client, db_session, tenant, work_item_factory
):
    items = _seed(db_session, work_item_factory, [("same.pdf", "COMPLETED") for _ in range(9)])
    for item in items:
        item.file_size = 4096
    db_session.commit()

    seen: list[str] = []
    for skip in range(0, 9, 2):
        page = _list(client, tenant, sort_by="file_size", sort_order="asc", skip=skip, limit=2)
        seen.extend(row["id"] for row in page["items"])

    assert sorted(seen) == sorted(str(item.id) for item in items)
    assert len(seen) == len(set(seen)) == 9


def test_viewer_totals_match_mine_only_and_workspace_scope(client, db_session, tenant, work_item_factory):
    """The count keeps its workspace scope: another workspace's documents never count."""
    _seed(db_session, work_item_factory, [("here.pdf", "COMPLETED")])
    foreign = work_item_factory(original_filename="here-too.pdf", workspace_id=tenant.foreign_workspace.id)
    foreign.status = "COMPLETED"
    db_session.commit()

    body = _list(client, tenant, search="here")

    assert [row["original_filename"] for row in body["items"]] == ["here.pdf"]
    assert body["totalItems"] == 1
    assert db_session.query(WorkItem).count() == 2
