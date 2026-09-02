"""ARCH-11.5 Step 6 — performance budgets assertion test suite."""

from __future__ import annotations

import os
import statistics
import time
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.request_context import STAGE_BUDGETS

BUDGET_MULTIPLIER = float(os.getenv("PERF_BUDGET_MULTIPLIER", "3.0"))
ITERATIONS = int(os.getenv("PERF_ITERATIONS", "20"))
WARMUP = 3
CORPUS_CHUNKS = 400


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


@pytest.fixture(scope="function")
def seeded_workspace(db_session: Session):
    from app.models.document_chunk import EMBEDDING_DIMENSION, DocumentChunk
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(
        name="perf", slug=f"perf-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush([org])
    workspace = Workspace(
        organization_id=org.id, slug="perf-ws", workspace_name="Perf",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(workspace)
    db_session.flush([workspace])
    item = WorkItem(
        workspace_id=workspace.id,
        original_filename="perf.pdf",
        stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=1024,
    )
    db_session.add(item)
    db_session.flush([item])

    for index in range(CORPUS_CHUNKS):
        db_session.add(
            DocumentChunk(
                workspace_id=workspace.id,
                organization_id=org.id,
                work_item_id=item.id,
                chunk_index=index,
                page_number=1 + index // 20,
                content=(
                    f"Clause {index}: the supplier shall deliver the goods "
                    f"within {index % 30 + 1} business days of the purchase "
                    f"order PO-2025-{4000 + index} being issued."
                ),
                token_count=28,
                embedding=[(index % 97) / 100.0] * EMBEDDING_DIMENSION,
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            )
        )
    db_session.flush()
    return workspace


def _measure(callable_, iterations: int = ITERATIONS) -> list[float]:
    for _ in range(WARMUP):
        callable_()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        callable_()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _assert_budget(name: str, samples: list[float]) -> None:
    budget = STAGE_BUDGETS[name] * BUDGET_MULTIPLIER
    p95 = _percentile(samples, 0.95)
    assert p95 <= budget, (
        f"{name} p95 {p95:.1f}ms exceeds {budget:.1f}ms "
        f"(base budget {STAGE_BUDGETS[name]}ms x {BUDGET_MULTIPLIER}). "
        f"median {statistics.median(samples):.1f}ms, "
        f"max {max(samples):.1f}ms over {len(samples)} runs."
    )


def test_lexical_search_within_budget(db_session, seeded_workspace):
    from app.services.lexical_search_service import lexical_search_service

    samples = _measure(
        lambda: lexical_search_service.full_text_search(
            db_session,
            workspace_id=seeded_workspace.id,
            query="supplier deliver goods business days",
            top_k=20,
        )
    )
    _assert_budget("retrieval", samples)


def test_trigram_search_within_budget(db_session, seeded_workspace):
    from app.services.lexical_search_service import lexical_search_service

    samples = _measure(
        lambda: lexical_search_service.trigram_search(
            db_session,
            workspace_id=seeded_workspace.id,
            query="PO-2025-4123",
            top_k=20,
        )
    )
    _assert_budget("retrieval", samples)


def test_dense_search_within_budget(db_session, seeded_workspace):
    from app.db.chunk_scope import nearest_chunks
    from app.models.document_chunk import EMBEDDING_DIMENSION

    probe = [0.42] * EMBEDDING_DIMENSION
    samples = _measure(
        lambda: nearest_chunks(
            db_session,
            workspace_id=seeded_workspace.id,
            embedding=probe,
            top_k=20,
        )
    )
    _assert_budget("retrieval.hybrid_sql", samples)


def test_vocabulary_derivation_within_budget(db_session, seeded_workspace):
    from app.services.vocabulary_service import workspace_vocabulary_service

    def build():
        workspace_vocabulary_service.invalidate(seeded_workspace.id)
        workspace_vocabulary_service.terms_for(db_session, seeded_workspace.id)

    samples = _measure(build, iterations=max(5, ITERATIONS // 4))
    _assert_budget("vocabulary", samples)


def test_context_assembly_within_budget(db_session, seeded_workspace):
    from app.core.config import settings
    from app.services.context_assembly_service import context_assembly_service

    results = [
        {
            "id": f"c{index}",
            "text": f"Clause {index}: the supplier shall deliver within days. " * 4,
            "metadata": {"original_filename": "perf.pdf", "page_number": 1},
        }
        for index in range(30)
    ]
    samples = _measure(
        lambda: context_assembly_service.assemble(
            results, max_characters=settings.RAG_MAX_CONTEXT_LENGTH
        )
    )
    _assert_budget("context_assembly", samples)


def test_citation_ranking_within_budget():
    from app.services.citation_service import citation_service

    results = [
        {
            "id": f"c{index}",
            "rrf_score": 0.02 - index * 0.0001,
            "similarity_score": 0.9 - index * 0.01,
            "rerank_score": None if index % 3 == 0 else 5.0 - index * 0.1,
        }
        for index in range(50)
    ]
    samples = _measure(lambda: citation_service.rank_citations(results))
    _assert_budget("citation", samples)
