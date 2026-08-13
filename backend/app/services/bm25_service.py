"""
BM25 Retrieval Service.
Provides sparse keyword retrieval for Hybrid RAG, strictly isolated per workspace.
"""

from __future__ import annotations

import logging
import uuid
import numpy as np
from collections import OrderedDict
from dataclasses import dataclass, field
from rank_bm25 import BM25Okapi

logger = logging.getLogger("app.services.document_processor")

_MAX_CACHED_INDEXES = 8


@dataclass
class _WorkspaceIndex:
    bm25: BM25Okapi | None = None
    documents: list[str] = field(default_factory=list)
    metadata: list[dict] = field(default_factory=list)


class BM25Service:
    """
    Sparse retrieval service, partitioned per workspace to guarantee multi-tenant security.
    """

    def __init__(self) -> None:
        self._indexes: OrderedDict[uuid.UUID, _WorkspaceIndex] = OrderedDict()

    def is_ready(self, *, workspace_id: uuid.UUID) -> bool:
        index = self._indexes.get(workspace_id)
        return index is not None and index.bm25 is not None

    def rebuild_index(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> None:
        # Lazy import to prevent pulling chromadb/sentence_transformers at module import time
        from app.services.embedding_service import embedding_service

        collection = embedding_service.get_workspace_collection(workspace_id)
        # Force Chroma to return documents and metadatas explicitly
        results = collection.get(include=["documents", "metadatas"])

        ids = results.get("ids", [])
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])

        if not documents:
            self._indexes.pop(workspace_id, None)
            return

        tokenized_documents = [
            document.lower().split()
            for document in documents
        ]

        index = _WorkspaceIndex(
            bm25=BM25Okapi(tokenized_documents),
            documents=documents,
            metadata=[
                {
                    "id": chunk_id,
                    **(metadata or {}),
                }
                for chunk_id, metadata in zip(ids, metadatas)
            ]
        )

        self._indexes[workspace_id] = index
        self._indexes.move_to_end(workspace_id)
        while len(self._indexes) > _MAX_CACHED_INDEXES:
            self._indexes.popitem(last=False)

        logger.info(
            "BM25 index for workspace %s rebuilt with %d chunks.",
            workspace_id,
            len(documents),
        )

    def search(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        top_k: int = 10,
        work_item_ids: list[str] | None = None,
    ) -> list[dict]:
        """
        Execute sparse keyword retrieval using BM25 strictly scoped inside a workspace.
        """
        index = self._indexes.get(workspace_id)
        if index is None or index.bm25 is None:
            self.rebuild_index(workspace_id=workspace_id)
            index = self._indexes.get(workspace_id)
            if index is None or index.bm25 is None:
                return []

        self._indexes.move_to_end(workspace_id)

        query_tokens = query.lower().split()
        scores = index.bm25.get_scores(query_tokens)
        ranked_indexes = np.argsort(scores)[::-1]

        results = []
        for idx in ranked_indexes:
            score = float(scores[idx])
            if score <= 0:
                continue

            metadata = index.metadata[idx]
            if (
                work_item_ids
                and metadata.get("work_item_id") not in work_item_ids
            ):
                continue

            results.append(
                {
                    "id": metadata["id"],
                    "text": index.documents[idx],
                    "metadata": metadata,
                    "bm25_score": score,
                }
            )

            if len(results) >= top_k:
                break

        logger.info(
            "BM25 returned %d result(s) for workspace %s.",
            len(results),
            workspace_id,
        )

        return results

    def invalidate(self, *, workspace_id: uuid.UUID) -> None:
        self._indexes.pop(workspace_id, None)


bm25_service = BM25Service()