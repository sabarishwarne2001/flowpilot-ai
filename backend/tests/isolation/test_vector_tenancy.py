"""ARCH-11 Step 8 — the tenant isolation gate matrix."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.chunk_scope import VectorScopeError, nearest_chunks, scoped_chunk_query
from app.models.document_chunk import EMBEDDING_DIMENSION, DocumentChunk
from app.services.hybrid_search_service import hybrid_search_service
from app.services.lexical_search_service import lexical_search_service

#: Deliberately near-identical. The numbers differ; nothing else does.
POLICY_A = [
    "Full-time employees accrue 1.75 days of annual leave per completed calendar month.",
    "A maximum of five unused days may be carried into the following leave year.",
    "Purchase Order PO-2025-4471 authorises spend of USD 128,400 to cost centre OPS-EU-03.",
    "Leave requests must be approved by a line manager before the absence begins.",
    "Unused entitlement is forfeited on 31 March and is not paid in lieu.",
]
POLICY_B = [
    "Full-time employees accrue 2.50 days of annual leave per completed calendar month.",
    "A maximum of ten unused days may be carried into the following leave year.",
    "Purchase Order PO-2025-9999 authorises spend of USD 44,000 to cost centre EU-01.",
    "Leave requests must be approved by a department head before the absence begins.",
    "Unused entitlement is forfeited on 30 June and is not paid in lieu.",
]

SMALL_TENANT_CHUNKS = 20


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSION


def _near(seed: float, jitter: float) -> list[float]:
    base = _vector(seed)
    base[0] = seed + jitter
    return base


def _make_tenant(
    db: Session, label: str, corpus: list[str], *, seed: float
) -> dict[str, Any]:
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(
        name=label,
        slug=f"{label}-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush([org])

    workspace = Workspace(
        organization_id=org.id,
        slug=f"{label}-ws",
        workspace_name=label.title(),
        status=WorkspaceStatus.ACTIVE,
    )
    db.add(workspace)
    db.flush([workspace])

    work_item = WorkItem(
        workspace_id=workspace.id,
        original_filename=f"{label}-leave-policy.pdf",
        stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=4096,
        extracted_text="\n\n".join(corpus),
    )
    db.add(work_item)
    db.flush([work_item])

    for index, content in enumerate(corpus):
        db.add(
            DocumentChunk(
                workspace_id=workspace.id,
                organization_id=org.id,
                work_item_id=work_item.id,
                chunk_index=index,
                page_number=1 + index // 3,
                content=content,
                token_count=max(1, len(content.split())),
                embedding=_near(seed, jitter=index / 1000),
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            )
        )
    db.flush()
    return {"org": org, "workspace": workspace, "work_item": work_item, "corpus": corpus}


def _make_empty(db: Session, label: str):
    from app.models.organization import Organization, OrganizationStatus
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(
        name=label, slug=f"{label}-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush([org])
    workspace = Workspace(
        organization_id=org.id, slug=f"{label}-ws",
        workspace_name=label.title(), status=WorkspaceStatus.ACTIVE,
    )
    db.add(workspace)
    db.flush([workspace])
    return org, workspace


@pytest.fixture()
def matrix(db_session: Session) -> dict[str, Any]:
    alpha = _make_tenant(db_session, "alpha", POLICY_A, seed=0.31)
    beta = _make_tenant(db_session, "beta", POLICY_B, seed=0.32)

    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    sibling = Workspace(
        organization_id=alpha["org"].id,
        slug="alpha-sibling",
        workspace_name="Alpha Finance",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(sibling)
    db_session.flush([sibling])

    sibling_item = WorkItem(
        workspace_id=sibling.id,
        original_filename="alpha-finance-policy.pdf",
        stored_filename=f"{alpha['org'].id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=2048,
        extracted_text="\n\n".join(POLICY_B),
    )
    db_session.add(sibling_item)
    db_session.flush([sibling_item])
    for index, content in enumerate(POLICY_B):
        db_session.add(
            DocumentChunk(
                workspace_id=sibling.id,
                organization_id=alpha["org"].id,
                work_item_id=sibling_item.id,
                chunk_index=index,
                page_number=1,
                content=content,
                token_count=max(1, len(content.split())),
                embedding=_near(0.31, jitter=index / 1000),
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            )
        )

    small = _make_tenant(
        db_session,
        "small",
        [f"Clause {i}: the supplier shall deliver within {i} business days." for i in range(SMALL_TENANT_CHUNKS)],
        seed=0.33,
    )

    empty_org, empty_ws = _make_empty(db_session, "empty")
    db_session.flush()

    return {
        "alpha": alpha,
        "beta": beta,
        "sibling": {"workspace": sibling, "work_item": sibling_item},
        "small": small,
        "empty": {"org": empty_org, "workspace": empty_ws},
    }


def _foreign_ids(matrix: dict[str, Any], own: str) -> set[str]:
    return {
        str(matrix[key]["work_item"].id)
        for key in ("alpha", "beta", "small")
        if key != own
    } | {str(matrix["sibling"]["work_item"].id)}


# ===========================================================================
# A.15.1 — dense retrieval
# ===========================================================================


def test_dense_search_never_crosses_an_organization(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        embedding=_vector(0.315),
        top_k=20,
    )
    assert hits, "isolation without completeness proves nothing"
    assert all(
        chunk.workspace_id == matrix["alpha"]["workspace"].id for chunk, _ in hits
    )
    assert all("2.50 days" not in chunk.content for chunk, _ in hits)


def test_dense_search_never_crosses_a_sibling_workspace(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        embedding=_vector(0.31),
        top_k=20,
    )
    assert hits
    foreign = str(matrix["sibling"]["work_item"].id)
    assert all(str(chunk.work_item_id) != foreign for chunk, _ in hits)


def test_small_tenant_receives_the_full_top_k(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["small"]["workspace"].id,
        embedding=_vector(0.33),
        top_k=10,
    )
    assert len(hits) == 10


def test_tenant_can_reach_every_one_of_its_own_chunks(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["small"]["workspace"].id,
        embedding=_vector(0.33),
        top_k=SMALL_TENANT_CHUNKS,
    )
    assert len(hits) == SMALL_TENANT_CHUNKS


def test_empty_tenant_gets_nothing_not_a_neighbour(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["empty"]["workspace"].id,
        embedding=_vector(0.31),
        top_k=10,
    )
    assert hits == []


# ===========================================================================
# A.15.2 — lexical retrieval
# ===========================================================================


def test_full_text_never_crosses_a_tenant(db_session, matrix):
    results = lexical_search_service.search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="annual leave accrued per calendar month",
        top_k=20,
    )
    assert results
    foreign = _foreign_ids(matrix, "alpha")
    assert all(result["work_item_id"] not in foreign for result in results)


def test_trigram_never_crosses_a_tenant(db_session, matrix):
    results = lexical_search_service.trigram_search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="PO-2025-9999",
        top_k=20,
    )
    assert all(
        chunk.workspace_id == matrix["alpha"]["workspace"].id for chunk, _ in results
    )
    assert all("PO-2025-9999" not in chunk.content for chunk, _ in results)


def test_each_tenant_sees_its_own_number(db_session, matrix):
    alpha = lexical_search_service.search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="days of annual leave accrued",
        top_k=5,
    )
    beta = lexical_search_service.search(
        db_session,
        workspace_id=matrix["beta"]["workspace"].id,
        query="days of annual leave accrued",
        top_k=5,
    )
    assert alpha and beta
    assert any("1.75 days" in r["text"] for r in alpha)
    assert any("2.50 days" in r["text"] for r in beta)
    assert not any("2.50 days" in r["text"] for r in alpha)
    assert not any("1.75 days" in r["text"] for r in beta)


# ===========================================================================
# A.15.3 — the fused pipeline
# ===========================================================================


def test_hybrid_fusion_never_crosses_a_tenant(db_session, matrix):
    outcome = hybrid_search_service.search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="how much annual leave is accrued each month",
        top_k=20,
    )
    assert outcome.results
    foreign = _foreign_ids(matrix, "alpha")
    assert all(r["work_item_id"] not in foreign for r in outcome.results)


def test_hybrid_fusion_is_complete_for_a_small_tenant(db_session, matrix):
    outcome = hybrid_search_service.search(
        db_session,
        workspace_id=matrix["small"]["workspace"].id,
        query="supplier delivery within business days",
        top_k=10,
    )
    assert len(outcome.results) == 10


def test_identifier_query_routes_to_the_fuzzy_arm_and_stays_in_tenant(
    db_session, matrix
):
    outcome = hybrid_search_service.search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="PO-2025-4471",
        top_k=10,
    )
    assert outcome.results
    assert any("PO-2025-4471" in r["text"] for r in outcome.results)
    assert all(
        r["work_item_id"] not in _foreign_ids(matrix, "alpha")
        for r in outcome.results
    )


def test_work_item_filter_cannot_reach_across_a_tenant(db_session, matrix):
    outcome = hybrid_search_service.search(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        query="annual leave",
        work_item_ids=[str(matrix["beta"]["work_item"].id)],
        top_k=10,
    )
    assert outcome.results == []


def test_organization_predicate_mismatch_returns_nothing(db_session, matrix):
    hits = nearest_chunks(
        db_session,
        workspace_id=matrix["alpha"]["workspace"].id,
        organization_id=matrix["beta"]["org"].id,
        embedding=_vector(0.31),
        top_k=10,
    )
    assert hits == []


# ===========================================================================
# A.15.4 — the helper cannot be bypassed
# ===========================================================================


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid"])
def test_query_without_a_workspace_is_refused(db_session, bad):
    with pytest.raises(VectorScopeError):
        scoped_chunk_query(db_session, bad)


def test_cascade_removes_chunks_when_a_workspace_is_deleted(db_session, matrix):
    workspace_id = matrix["beta"]["workspace"].id
    db_session.delete(matrix["beta"]["workspace"])
    db_session.flush()
    remaining = db_session.execute(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.workspace_id == workspace_id
        )
    ).scalar_one()
    assert remaining == 0


# ===========================================================================
# A.15.5 — the negative control
# ===========================================================================


def test_negative_control_proves_the_gate_can_fail(db_session, matrix):
    """An unscoped query MUST leak data across tenants."""
    probe = _vector(0.315)
    rows = db_session.execute(
        text(
            "SELECT workspace_id, content FROM document_chunks "
            "ORDER BY embedding <=> CAST(:probe AS vector) LIMIT 20"
        ),
        {"probe": str(probe)},
    ).all()

    workspaces = {row[0] for row in rows}
    assert len(workspaces) > 1, "unscoped control query failed to detect cross-tenant leak"
    assert any("2.50 days" in row[1] for row in rows)


def test_iterative_scan_is_actually_applied(db_session, matrix):
    from app.db.chunk_scope import ensure_iterative_scan

    ensure_iterative_scan(db_session)
    setting = db_session.execute(text("SHOW hnsw.iterative_scan")).scalar_one()
    assert setting == "relaxed_order"
