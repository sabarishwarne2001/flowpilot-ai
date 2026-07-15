"""
Central Retrieval Service.

Provides a single entry point for all document retrieval.

Current providers:

- Dense Vector Search (ChromaDB)

Future providers:

- BM25
- PostgreSQL Full Text Search
- Elasticsearch
- OpenSearch

The Assistant should never call the Embedding Service directly.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from typing import Any

from collections import defaultdict

from app.core.config import settings
from app.services.embedding_service import embedding_service
from app.services.bm25_service import bm25_service
from app.services.intent_service import intent_service
from app.services.reranker_service import (
    reranker_service,
)

import logging

logger = logging.getLogger(__name__)

class RetrievalService:

    """
    Production retrieval abstraction.
    """

    def _lexical_score(
        self,
        *,
        query: str,
        text: str,
        filename: str,
    ) -> float:
        """
        Compute a lightweight lexical relevance score.

        This complements embedding similarity by rewarding
        exact keyword matches.
        """

        query_words = {
            word.lower()
            for word in query.split()
            if len(word) >= 3
        }

        text_lower = text.lower()
        filename_lower = filename.lower()

        score = 0.0

        #
        # Exact filename matches
        #
        for word in query_words:

            if word in filename_lower:
                score += 2.0

        #
        # Exact text matches
        #
        for word in query_words:

            if word in text_lower:
                score += 1.0

        return score


    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        similarity_threshold: float,
        filter_work_item_id: UUID | None = None,
        filter_work_item_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve candidate chunks.

        Current implementation:
            Dense vector retrieval.

        Future implementation:
            Dense + Sparse hybrid retrieval.
        """

        dense_results = embedding_service.similarity_search(
            query=query,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            filter_work_item_id=filter_work_item_id,
            filter_work_item_ids=filter_work_item_ids,
        )

        #
        # Future:
        #
        # sparse_results = bm25.retrieve(...)
        #
        # merged = hybrid_rank(...)
        #
        # return merged
        #

        for result in dense_results:

            metadata = result.get(
                "metadata",
                {},
            )

            lexical_score = self._lexical_score(
                query=query,
                text=result["text"],
                filename=metadata.get(
                    "original_filename",
                    "",
                ),
            )

            result["lexical_score"] = lexical_score

        return dense_results
    

    def hybrid_search(
        self,
        *,
        query: str,
        work_item_ids: list[str],
        top_k: int,
        similarity_threshold: float,
    ):
        """
        Hybrid retrieval:
        - Semantic search
        - Keyword search
        - Merge results
        """

        intent = intent_service.detect_intent(
            query,
        )

        logger.info(
            "Detected retrieval intent: %s",
            intent,
        )

        semantic_results = embedding_service.similarity_search(
            query=query,
            top_k=top_k,
            filter_work_item_ids=[
                uuid.UUID(work_item_id)
                for work_item_id in work_item_ids
            ],
            similarity_threshold=similarity_threshold,
        )

        lexical_results = bm25_service.search(
            query=query,
            work_item_ids=work_item_ids,
            top_k=top_k,
        )

        merged_results = self._rrf_merge(
            semantic_results=semantic_results,
            lexical_results=lexical_results,
        )

        merged_results = self._boost_intent_documents(
            merged_results,
            intent,
        )

        merged_results = reranker_service.rerank(
            query=query,
            results=merged_results,
        )

        reranker_service.log_top_results(
            merged_results,
            top_n=3,
        )

        document_count = len(
            {
                result["metadata"]["work_item_id"]
                for result in merged_results
            }
        )

        if document_count > 1:

            logger.info(
                "Applying context balancing across %d documents.",
                document_count,
            )

            merged_results = self._balance_documents(
                merged_results,
            )

        else:

            logger.info(
                "Skipping context balancing (single document).",
            )

            logger.info(
                "Hybrid retrieval pipeline completed with %d final chunk(s).",
                len(merged_results),
            )

        return merged_results
    

    def _rrf_merge(
        self,
        *,
        semantic_results: list[dict],
        lexical_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """
        Merge semantic and BM25 retrieval using
        Reciprocal Rank Fusion (RRF).

        This avoids score normalization and is
        considered the production standard for
        hybrid retrieval.
        """

        fused_scores: dict[str, float] = {}

        result_lookup: dict[str, dict] = {}

        #
        # Semantic ranking
        #
        for rank, result in enumerate(
            semantic_results,
            start=1,
        ):

            chunk_id = result["id"]

            fused_scores.setdefault(
                chunk_id,
                0.0,
            )

            fused_scores[chunk_id] += 1 / (
                k + rank
            )

            result_lookup[chunk_id] = result

        #
        # BM25 ranking
        #
        for rank, result in enumerate(
            lexical_results,
            start=1,
        ):

            chunk_id = result["id"]

            fused_scores.setdefault(
                chunk_id,
                0.0,
            )

            fused_scores[chunk_id] += 1 / (
                k + rank
            )

            result_lookup[chunk_id] = result

        for chunk_id, result in result_lookup.items():

            result["rrf_score"] = fused_scores[
                chunk_id
            ]
            
        merged = sorted(
            result_lookup.values(),
            key=lambda item: fused_scores[
                item["id"]
            ],
            reverse=True,
        )

        logger.info(
            "Hybrid retrieval produced %d merged result(s).",
            len(merged),
        )

        for result in merged:

            logger.info(
                "RRF %.5f | %s | Chunk %s",
                result["rrf_score"],
                result["metadata"].get(
                    "original_filename"
                ),
                result["metadata"].get(
                    "chunk_index"
                ),
            )

        return merged


    def _boost_intent_documents(
        self,
        results: list[dict],
        intent: str,
    ) -> list[dict]:
        """
        Boost documents matching the detected query intent.
        """

        if intent == "unknown":
            return results

        boosted = []

        for result in results:

            metadata = result.get(
                "metadata",
                {},
            )

            filename = metadata.get(
                "original_filename",
                "",
            ).lower()

            score = result.get(
                "rrf_score",
                0.0,
            )

            if intent in filename:
                score += 1.0

            result["rrf_score"] = score

            boosted.append(result)

        boosted.sort(
            key=lambda x: x.get(
                "rrf_score",
                0.0,
            ),
            reverse=True,
        )

        return boosted
    

    def _balance_documents(
        self,
        results: list[dict],
    ) -> list[dict]:
        """
        Balance retrieved chunks across documents while
        preserving their relevance order within each document.

        This prevents one document from dominating the
        context sent to the LLM.
        """

        grouped: dict[str, list[dict]] = defaultdict(list)

        #
        # Group chunks by Work Item.
        #
        for result in results:

            metadata = result.get(
                "metadata",
                {},
            )

            work_item_id = metadata.get(
                "work_item_id",
            )

            grouped[
                work_item_id
            ].append(result)

        for chunks in grouped.values():

            chunks.sort(
                key=lambda item: item.get(
                    "rerank_score",
                    0.0,
                ),
                reverse=True,
            )

        balanced: list[dict] = []

        for work_item_id, chunks in grouped.items():

            grouped[work_item_id] = chunks[
                : settings.MAX_CONTEXT_CHUNKS_PER_DOCUMENT
            ]

        while grouped:

            completed = []

            for work_item_id, chunks in grouped.items():

                if chunks:

                    balanced.append(
                        chunks.pop(0)
                    )

                if not chunks:

                    completed.append(
                        work_item_id
                    )

            for work_item_id in completed:

                grouped.pop(
                    work_item_id,
                    None,
                )

        logger.info(
            "Balanced %d chunks across %d document(s).",
            len(balanced),
            len(
                {
                    r["metadata"][
                        "work_item_id"
                    ]
                    for r in balanced
                }
            ),
        )

        logger.info(
            "Context balancing completed."

        )

        for result in balanced:

            metadata = result.get(
                "metadata",
                {},
            )

            logger.info(
                "%s | Page %s | Chunk %s | %.4f",
                metadata.get(
                    "original_filename",
                ),
                metadata.get(
                    "page_number",
                ),
                metadata.get(
                    "chunk_index",
                ),
                result.get(
                    "rerank_score",
                    0.0,
                ),
            )

        return balanced


retrieval_service = RetrievalService()