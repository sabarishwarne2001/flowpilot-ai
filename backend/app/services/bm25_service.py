"""
BM25 Retrieval Service.

Provides sparse keyword retrieval for Hybrid RAG.
"""

from __future__ import annotations
from rank_bm25 import BM25Okapi
from uuid import UUID
from app.services.embedding_service import embedding_service
import logging
import numpy as np

logger = logging.getLogger(__name__)

class BM25Service:

    """
    Sparse retrieval service.
    """

    def __init__(self) -> None:

        self._bm25: BM25Okapi | None = None

        self._documents: list[str] = []

        self._metadata: list[dict] = []

    def is_ready(self) -> bool:

        return self._bm25 is not None
    
    def rebuild_index(
        self,
    ) -> None:
        """
        Rebuild the BM25 index from every chunk currently
        stored inside ChromaDB.

        This method is called whenever documents are added
        or deleted.
        """

        collection = embedding_service.get_default_collection()

        results = collection.get()

        ids = results.get(
            "ids",
            [],
        )

        documents = results.get(
            "documents",
            [],
        )

        metadatas = results.get(
            "metadatas",
            [],
        )

        if not documents:

            self._bm25 = None
            self._documents = []
            self._metadata = []

            return
        
        tokenized_documents = [
            document.lower().split()
            for document in documents
        ]

        self._bm25 = BM25Okapi(
            tokenized_documents,
        )

        self._documents = documents

        self._metadata = [
            {
                "id": chunk_id,
                **(metadata or {}),
            }
            for chunk_id, metadata in zip(
                ids,
                metadatas,
            )
        ]

        logger.info(
            "BM25 index rebuilt with %d chunks.",
            len(documents),
        )

    def search(
        self,
        *,
        query: str,
        top_k: int = 10,
        work_item_ids: list[str] | None = None,
    ) -> list[dict]:
        """
        Execute sparse keyword retrieval using BM25.

        Returns a normalized retrieval format that matches
        the semantic retrieval service.
        """

        if self._bm25 is None:
            return []

        query_tokens = query.lower().split()

        scores = self._bm25.get_scores(
            query_tokens,
        )

        ranked_indexes = np.argsort(
            scores
        )[::-1]

        results = []

        for index in ranked_indexes:

            score = float(scores[index])

            if score <= 0:
                continue

            metadata = self._metadata[index]

            if (
                work_item_ids
                and metadata.get("work_item_id")
                not in work_item_ids
            ):
                continue

            results.append(
                {
                    "id": metadata["id"],
                    "text": self._documents[index],
                    "metadata": metadata,
                    "bm25_score": score,
                }
            )

            if len(results) >= top_k:
                break

        logger.info(
            "BM25 returned %d result(s).",
            len(results),
        )

        return results

bm25_service = BM25Service()