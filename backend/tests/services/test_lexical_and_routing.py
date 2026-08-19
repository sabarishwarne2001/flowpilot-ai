"""ARCH-11 Steps 4-5 — lexical search, dual-read routing, and the backfill."""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.document_chunk import EMBEDDING_DIMENSION, DocumentChunk
from app.services.chunk_retrieval_service import (
    chunk_public_id,
    chunk_retrieval_service,
    knowledge_router,
)
from app.services.lexical_search_service import lexical_search_service

CORPUS = [
    "Full-time employees accrue 1.75 days of annual leave per completed calendar month.",
    "A maximum of five unused days may be carried into the following leave year.",
    "Purchase Order PO-2025-4471 authorises spend of USD 128,400 against cost centre OPS-EU-03.",
    "Requests for leave must be approved by a line manager before the absence begins.",
    "The vendor shall indemnify the customer against third-party intellectual property claims.",
]

FOREIGN_CORPUS = [
    "Full-time employees accrue 2.50 days of annual leave per completed calendar month.",
    "Purchase Order PO-2025-9999 authorises spend of USD 44,000 against cost centre EU-01.",
]


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSION


@pytest.fixture()
def tenants(db_session: Session):
    """Two organizations, one workspace each, with near-identical documents."""
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    built = {}
    for label, corpus in (("alpha", CORPUS), ("beta", FOREIGN_CORPUS)):
        org = Organization(
            name=label,
            slug=f"{label}-{uuid.uuid4().hex[:8]}",
            status=OrganizationStatus.ACTIVE,
        )
        db_session.add(org)
        db_session.flush([org])

        workspace = Workspace(
            organization_id=org.id,
            slug=f"{label}-ws",
            workspace_name=label.title(),
            status=WorkspaceStatus.ACTIVE,
        )
        db_session.add(workspace)
        db_session.flush([workspace])

        work_item = WorkItem(
            workspace_id=workspace.id,
            original_filename=f"{label}-policy.pdf",
            stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
            file_type="application/pdf",
            file_size=2048,
            extracted_text="\n\n".join(corpus),
        )
        db_session.add(work_item)
        db_session.flush([work_item])

        for index, content in enumerate(corpus):
            db_session.add(
                DocumentChunk(
                    workspace_id=workspace.id,
                    organization_id=org.id,
                    work_item_id=work_item.id,
                    chunk_index=index,
                    page_number=1,
                    page_start_char=index * 100,
                    page_end_char=index * 100 + len(content),
                    content=content,
                    token_count=max(1, len(content.split())),
                    embedding=_vector(0.1 * (index + 1)),
                    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                )
            )
        db_session.flush()
        built[label] = {"org": org, "workspace": workspace, "work_item": work_item}
    return built


# ===========================================================================
# Full text
# ===========================================================================


def test_full_text_finds_a_stemmed_match(db_session, tenants):
    hits = lexical_search_service.full_text_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="approving leave requests",
        top_k=5,
    )
    assert hits
    assert any("approved by a line manager" in chunk.content for chunk, _ in hits)


def test_full_text_ranks_the_better_match_first(db_session, tenants):
    hits = lexical_search_service.full_text_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="carried unused leave days",
        top_k=5,
    )
    assert hits
    assert "carried into the following leave year" in hits[0][0].content


def test_ts_rank_cd_is_bounded(db_session, tenants):
    hits = lexical_search_service.full_text_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="leave",
        top_k=5,
    )
    assert hits
    assert all(0.0 <= score < 1.0 for _, score in hits)


@pytest.mark.parametrize("hostile", ["", "   ", "&&&", "!!!", "|||", "(", "a & | b"])
def test_hostile_input_never_raises(db_session, tenants, hostile):
    assert (
        lexical_search_service.full_text_search(
            db_session,
            workspace_id=tenants["alpha"]["workspace"].id,
            query=hostile,
            top_k=5,
        )
        is not None
    )


def test_quoted_phrase_is_honoured(db_session, tenants):
    hits = lexical_search_service.full_text_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query='"line manager"',
        top_k=5,
    )
    assert any("line manager" in chunk.content for chunk, _ in hits)


# ===========================================================================
# Trigram — the arm full text is structurally bad at
# ===========================================================================


def test_trigram_finds_an_identifier(db_session, tenants):
    hits = lexical_search_service.trigram_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="PO-2025-4471",
        top_k=5,
    )
    assert hits
    assert "PO-2025-4471" in hits[0][0].content


def test_trigram_tolerates_one_wrong_character(db_session, tenants):
    hits = lexical_search_service.trigram_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="PO-2025-4471".replace("7", "1"),
        top_k=5,
        threshold=0.5,
    )
    assert any("PO-2025-4471" in chunk.content for chunk, _ in hits)


def test_trigram_ignores_very_short_queries(db_session, tenants):
    assert (
        lexical_search_service.trigram_search(
            db_session, workspace_id=tenants["alpha"]["workspace"].id, query="PO", top_k=5
        )
        == []
    )


def test_search_labels_which_arm_produced_each_hit(db_session, tenants):
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="PO-2025-4471",
        top_k=5,
    )
    assert results
    assert {result.get("lexical_match") for result in results} <= {
        "full_text",
        "trigram",
    }


# ===========================================================================
# Tenancy
# ===========================================================================


def test_lexical_search_never_crosses_a_tenant(db_session, tenants):
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave accrued per calendar month",
        top_k=10,
    )
    assert results
    foreign = str(tenants["beta"]["work_item"].id)
    assert all(result["work_item_id"] != foreign for result in results)
    assert all("2.50 days" not in result["text"] for result in results)


def test_both_tenants_get_their_own_answer(db_session, tenants):
    alpha = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave accrual",
        top_k=10,
    )
    beta = lexical_search_service.search(
        db_session,
        workspace_id=tenants["beta"]["workspace"].id,
        query="annual leave accrual",
        top_k=10,
    )
    assert alpha and beta
    assert {r["id"] for r in alpha}.isdisjoint({r["id"] for r in beta})


def test_empty_tenant_returns_nothing_not_a_neighbour(db_session, tenants):
    from app.models.organization import Organization, OrganizationStatus
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(
        name="gamma", slug=f"gamma-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush([org])
    empty = Workspace(
        organization_id=org.id, slug="gamma-ws", workspace_name="Gamma",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(empty)
    db_session.flush([empty])

    assert (
        lexical_search_service.search(
            db_session, workspace_id=empty.id, query="annual leave", top_k=10
        )
        == []
    )


def test_work_item_filter_is_respected(db_session, tenants):
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave",
        work_item_ids=[str(uuid.uuid4())],
        top_k=10,
    )
    assert results == []


# ===========================================================================
# The property BM25 could not have — §0.2
# ===========================================================================


def test_index_survives_a_new_session(db_session, tenants):
    """A second session sees the same lexical index with no rebuild.

    `BM25Service.rebuild_index` was called from `document.enrich` and populated
    a dict inside that worker process. Nothing else could ever see it. This is
    that property, inverted and asserted.
    """
    workspace_id = tenants["alpha"]["workspace"].id
    db_session.flush()

    other = Session(bind=db_session.connection())
    try:
        hits = lexical_search_service.full_text_search(
            other, workspace_id=workspace_id, query="annual leave", top_k=5
        )
        assert hits, "a second session must see the index without rebuilding it"
    finally:
        other.close()


def test_no_rebuild_entry_point_exists():
    """There is no index to rebuild, and there must never be one."""
    assert not hasattr(lexical_search_service, "rebuild_index")
    assert not hasattr(lexical_search_service, "invalidate")


def test_generated_tsvector_updates_when_content_changes(db_session, tenants):
    chunk = db_session.execute(
        text(
            "SELECT id FROM document_chunks WHERE workspace_id = :ws "
            "AND chunk_index = 0"
        ),
        {"ws": str(tenants["alpha"]["workspace"].id)},
    ).scalar_one()
    db_session.execute(
        text(
            "UPDATE document_chunks SET content = :c "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {
            "c": "Sabbatical entitlement is granted after seven years of service.",
            "ws": str(tenants["alpha"]["workspace"].id),
            "id": str(chunk),
        },
    )
    db_session.flush()
    hits = lexical_search_service.full_text_search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="sabbatical entitlement",
        top_k=5,
    )
    assert hits


# ===========================================================================
# Dual-read routing
# ===========================================================================


def test_router_splits_by_document_not_by_workspace(db_session, tenants):
    indexed = str(tenants["alpha"]["work_item"].id)
    unindexed = str(uuid.uuid4())
    plan = knowledge_router.plan(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        work_item_ids=[indexed, unindexed],
    )
    assert plan.indexed == (indexed,)
    assert plan.legacy == (unindexed,)
    assert plan.is_mixed


def test_router_falls_back_to_chroma_without_a_session(tenants):
    plan = knowledge_router.plan(
        None,
        workspace_id=tenants["alpha"]["workspace"].id,
        work_item_ids=["a", "b"],
    )
    assert plan.indexed == ()
    assert plan.legacy == ("a", "b")


def test_router_respects_the_rollback_flag(db_session, tenants, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "KNOWLEDGE_DUAL_READ", False)
    plan = knowledge_router.plan(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        work_item_ids=[str(tenants["alpha"]["work_item"].id)],
    )
    assert plan.indexed == ()
    assert len(plan.legacy) == 1


# ===========================================================================
# Result shape
# ===========================================================================


def test_pgvector_results_match_the_chroma_contract(db_session, tenants):
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave",
        top_k=3,
    )
    assert results
    required = {
        "id",
        "text",
        "document_name",
        "work_item_id",
        "chunk_index",
        "page_number",
        "metadata",
    }
    assert required <= set(results[0])
    assert results[0]["metadata"]["original_filename"] == "alpha-policy.pdf"
    assert results[0]["metadata"]["source"] == "pgvector"


def test_chunk_ids_match_the_chroma_scheme(db_session, tenants):
    work_item_id = tenants["alpha"]["work_item"].id
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave",
        top_k=3,
    )
    assert results[0]["id"].startswith(f"{work_item_id}_chunk_")
    assert chunk_public_id(work_item_id, 0) == f"{work_item_id}_chunk_0"


def test_provenance_reaches_the_result(db_session, tenants):
    results = lexical_search_service.search(
        db_session,
        workspace_id=tenants["alpha"]["workspace"].id,
        query="annual leave",
        top_k=3,
    )
    metadata = results[0]["metadata"]
    assert "page_start_char" in metadata
    assert "page_end_char" in metadata
    assert "bbox" in metadata