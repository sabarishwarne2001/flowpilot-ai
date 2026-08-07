"""
Hybrid RAG retrieval (Dense & Sparse) isolation integration tests.
"""

import pytest


@pytest.fixture
def stub_embedder(monkeypatch):
    def fake(texts: list[str]) -> list[list[float]]:
        return [
            [((hash(t) >> i) & 0xFF) / 255.0 for i in range(0, 32, 4)]
            for t in texts
        ]
    from app.services.embedding_service import EmbeddingService
    monkeypatch.setattr(
        EmbeddingService,
        "generate_embeddings",
        lambda self, texts: fake(texts),
    )


@pytest.fixture
def embedded(stub_embedder, alpha_ws, beta_ws):
    from app.services.bm25_service import bm25_service
    from app.services.embedding_service import embedding_service
    from app.services.document_models import DocumentChunk
    import uuid

    # Multi-document corpus prevents standard BM25 negative/zero IDF score clipping
    chunks_a = [
        DocumentChunk(text="ALPHA SECRET PHRASE ZEBRA", chunk_index=0, page_number=1),
        DocumentChunk(text="ALPHA DUMMY ONE", chunk_index=1, page_number=1),
        DocumentChunk(text="ALPHA DUMMY TWO", chunk_index=2, page_number=1),
    ]
    chunks_b = [
        DocumentChunk(text="BETA SECRET PHRASE QUOKKA", chunk_index=0, page_number=1),
        DocumentChunk(text="BETA DUMMY ONE", chunk_index=1, page_number=1),
        DocumentChunk(text="BETA DUMMY TWO", chunk_index=2, page_number=1),
    ]

    emb_a = embedding_service.generate_embeddings([c.text for c in chunks_a])
    emb_b = embedding_service.generate_embeddings([c.text for c in chunks_b])

    dummy_item_a = uuid.uuid4()
    dummy_item_b = uuid.uuid4()

    embedding_service.store_chunks(
        workspace_id=alpha_ws.id,
        work_item_id=dummy_item_a,
        original_filename="alpha_doc.pdf",
        chunks=chunks_a,
        embeddings=emb_a,
    )
    embedding_service.store_chunks(
        workspace_id=beta_ws.id,
        work_item_id=dummy_item_b,
        original_filename="beta_doc.pdf",
        chunks=chunks_b,
        embeddings=emb_b,
    )

    bm25_service.rebuild_index(workspace_id=alpha_ws.id)
    bm25_service.rebuild_index(workspace_id=beta_ws.id)


def test_dense_retrieval_is_isolated(embedded, alpha_ws, beta_ws):
    from app.services.embedding_service import embedding_service

    own = embedding_service.similarity_search(
        workspace_id=alpha_ws.id, query="ALPHA SECRET PHRASE ZEBRA", top_k=10
    )
    assert any("ZEBRA" in r["text"] for r in own), "positive control failed"

    foreign = embedding_service.similarity_search(
        workspace_id=beta_ws.id, query="ALPHA SECRET PHRASE ZEBRA", top_k=10
    )
    assert not any("ZEBRA" in r["text"] for r in foreign)


def test_sparse_retrieval_is_isolated(embedded, alpha_ws, beta_ws):
    from app.services.bm25_service import bm25_service

    own = bm25_service.search(
        workspace_id=alpha_ws.id, query="ZEBRA", top_k=10
    )
    assert any("ZEBRA" in r["text"] for r in own), "positive control failed"

    foreign = bm25_service.search(
        workspace_id=beta_ws.id, query="ZEBRA", top_k=10
    )
    assert not any("ZEBRA" in r["text"] for r in foreign)


def test_bm25_rebuild_does_not_cross_workspaces(embedded, alpha_ws, beta_ws):
    from app.services.bm25_service import bm25_service

    bm25_service.rebuild_index(workspace_id=alpha_ws.id)
    beta_res = bm25_service.search(
        workspace_id=beta_ws.id, query="QUOKKA", top_k=10
    )
    assert any("QUOKKA" in r["text"] for r in beta_res)