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
from app.services.query_service import (
    query_service,
)
from app.services.reranker_service import (
    reranker_service,
)
from app.services.document_filter_service import (
    document_filter_service,
)

import logging

logger = logging.getLogger(__name__)

class RetrievalService:

    """
    Production retrieval abstraction.
    """

    def _determine_similarity_threshold(
        self,
        *,
        query: str,
    ) -> float:
        """
        Production adaptive similarity threshold.

        Short queries are inherently ambiguous and
        require a lower threshold to avoid missing
        relevant candidates.

        Long queries are more specific and can use
        a stricter threshold.
        """

        words = len(query.split())

        if words <= 2:
            return 0.12

        if words <= 5:
            return 0.18

        if words <= 10:
            return 0.22

        return 0.28
    

    def _retry_similarity_search(
        self,
        *,
        query: str,
        top_k: int,
        similarity_threshold: float,
        filter_work_item_id: UUID | None = None,
        filter_work_item_ids: list[UUID] | None = None,
        collection_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Production retrieval retry.

        Retry with progressively relaxed thresholds
        when too few semantic candidates are found.
        """

        retry_thresholds = [

            similarity_threshold,

            max(
                0.08,
                similarity_threshold - 0.05,
            ),

            max(
                0.05,
                similarity_threshold - 0.10,
            ),
        ]

        best_results: list[dict[str, Any]] = []

        for threshold in retry_thresholds:

            logger.info(
                "Semantic retrieval attempt (threshold=%.3f)",
                threshold,
            )

            results = embedding_service.similarity_search(
                query=query,
                top_k=top_k,
                similarity_threshold=threshold,
                filter_work_item_id=filter_work_item_id,
                filter_work_item_ids=filter_work_item_ids,
                collection_name=collection_name,
            )

            if len(results) > len(best_results):

                best_results = results

            if len(results) >= 8:

                logger.info(
                    "Retrieval retry satisfied with %d candidates.",
                    len(results),
                )

                return results

        logger.info(
            "Returning best retry result (%d candidates).",
            len(best_results),
        )

        return best_results


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
    

    def _determine_candidate_count(
        self,
        *,
        query: str,
    ) -> int:
        """
        Determine how many semantic candidates should
        be retrieved before hybrid ranking.

        Short and ambiguous queries require more
        candidates than long, specific queries.
        """

        word_count = len(query.split())

        if word_count <= 2:
            return 25

        if word_count <= 5:
            return 18

        if word_count <= 10:
            return 14

        return 10


    def _semantic_multi_query_search(
        self,
        *,
        query: str,
        top_k: int,
        similarity_threshold: float,
        filter_work_item_id: UUID | None = None,
        filter_work_item_ids: list[UUID] | None = None,
        collection_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute semantic retrieval using multiple
        query variants.

        Candidate chunks are merged and deduplicated
        before entering the hybrid retrieval pipeline.
        """

        search_queries = (
            query_service.generate_search_queries(
                query,
            )
        )

        candidate_count = (
            self._determine_candidate_count(
                query=query,
            )
        )

        adaptive_threshold = (
            self._determine_similarity_threshold(
                query=query,
            )
        )

        similarity_threshold = min(
            similarity_threshold,
            adaptive_threshold,
        )

        logger.info(
            "Adaptive similarity threshold: %.3f",
            similarity_threshold,
        )

        logger.info(
            "Adaptive candidate count: %d",
            candidate_count,
        )

        merged: dict[str, dict[str, Any]] = {}

        for search_query in search_queries:

            logger.info(
                "Semantic search using query: %s",
                search_query,
            )

            results = self._retry_similarity_search(
                query=search_query,
                top_k=max(
                    top_k,
                    candidate_count,
                ),
                similarity_threshold=similarity_threshold,
                filter_work_item_id=filter_work_item_id,
                filter_work_item_ids=filter_work_item_ids,
                collection_name=collection_name,
            )

            for result in results:

                chunk_id = result["id"]

                existing = merged.get(
                    chunk_id,
                )

                if existing is None:

                    merged[
                        chunk_id
                    ] = result

                    continue

                if (
                    result.get(
                        "similarity_score",
                        0.0,
                    )
                    >
                    existing.get(
                        "similarity_score",
                        0.0,
                    )
                ):

                    merged[
                        chunk_id
                    ] = result

        merged_results = sorted(
            merged.values(),
            key=lambda item: item.get(
                "similarity_score",
                0.0,
            ),
            reverse=True,
        )

        logger.info(
            "Multi-query semantic retrieval produced %d unique chunk(s).",
            len(
                merged_results,
            ),
        )

        return merged_results


    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        similarity_threshold: float,
        filter_work_item_id: UUID | None = None,
        filter_work_item_ids: list[UUID] | None = None,
        collection_name: str | None = None,
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
            collection_name=collection_name,
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
        collection_name: str | None = None,
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

        semantic_results = (
            self._semantic_multi_query_search(
                query=query,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                filter_work_item_ids=[
                    uuid.UUID(work_item_id)
                    for work_item_id in work_item_ids
                ], 
                collection_name=collection_name,

            )
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

        #
        # Estimate baseline retrieval confidence from retrieval signals.
        #
        merged_results = (
            self._estimate_retrieval_confidence(
                merged_results,
            )
        )

        #
        # Apply metadata prior on top of the baseline confidence.
        #
        merged_results = (
            self._apply_metadata_prior(
                query=query,
                results=merged_results,
            )
        )

        #
        # Apply document-level confidence prior.
        #
        merged_results = (
            self._apply_document_prior(
                merged_results,
            )
        )

        reranker_service.log_top_results(
            merged_results,
            top_n=3,
        )

        #
        # Cross-document filtering.
        #
        merged_results = (
            document_filter_service.filter_documents(
                merged_results,
            )
        )

        #
        # Context balancing.
        #
        document_count = len(
            {
                result["metadata"]["work_item_id"]
                for result in merged_results
            }
        )

        if (
            document_count > 1
            and self._should_balance_context(
                merged_results,
            )
        ):

            logger.info(
                "Applying context balancing across %d documents.",
                document_count,
            )

            merged_results = self._balance_documents(
                merged_results,
            )

        else:

            logger.info(
                "Skipping context balancing.",
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
    

    def _estimate_retrieval_confidence(
        self,
        results: list[dict],
    ) -> list[dict]:
        """
        Estimate retrieval confidence using multiple retrieval
        signals.

        This confidence is later consumed by document filtering,
        citation ranking and assistant response generation.

        Confidence is normalized into [0,1].
        """

        if not results:
            return results

        rerank_scores = [
            float(
                result.get(
                    "rerank_score",
                    0.0,
                )
            )
            for result in results
        ]

        semantic_scores = [
            float(
                result.get(
                    "similarity_score",
                    0.0,
                )
            )
            for result in results
        ]

        lexical_scores = [
            float(
                result.get(
                    "lexical_score",
                    0.0,
                )
            )
            for result in results
        ]

        max_rerank = max(rerank_scores) if rerank_scores else 1.0
        min_rerank = min(rerank_scores) if rerank_scores else 0.0

        max_lexical = max(lexical_scores) if lexical_scores else 1.0

        rerank_range = max(
            max_rerank - min_rerank,
            1e-6,
        )

        lexical_range = max(
            max_lexical,
            1e-6,
        )

        for result in results:

            rerank = (
                float(
                    result.get(
                        "rerank_score",
                        0.0,
                    )
                )
                - min_rerank
            ) / rerank_range

            semantic = float(
                result.get(
                    "similarity_score",
                    0.0,
                )
            )

            lexical = (
                float(
                    result.get(
                        "lexical_score",
                        0.0,
                    )
                )
                / lexical_range
            )

            confidence = (
                0.55 * rerank
                +
                0.30 * semantic
                +
                0.15 * lexical
            )

            confidence = max(
                0.0,
                min(
                    confidence,
                    1.0,
                ),
            )

            result[
                "retrieval_confidence"
            ] = confidence

        logger.info(
            "Retrieval confidence estimated for %d chunks.",
            len(results),
        )

        return results
    

    def _apply_metadata_prior(
        self,
        *,
        query: str,
        results: list[dict],
    ) -> list[dict]:
        """
        Production metadata-aware document prior.

        Boosts retrieval using metadata without overriding
        semantic relevance.

        Signals:
        - filename
        - document title
        - document keywords
        """

        if not results:
            return results

        query_words = {
            word.lower()
            for word in query.split()
            if len(word) >= 3
        }

        for result in results:

            metadata = result.get(
                "metadata",
                {},
            )

            filename = metadata.get(
                "original_filename",
                "",
            ).lower()

            prior = 0.0

            #
            # Filename prior.
            #
            filename_tokens = {
                token
                for token in filename.replace(
                    "_",
                    " ",
                ).replace(
                    "-",
                    " ",
                ).split()
            }

            overlap = len(
                query_words
                &
                filename_tokens
            )

            if overlap:

                prior += min(
                    overlap * 0.35,
                    1.00,
                )

            #
            # Combine with confidence.
            #
            result[
                "retrieval_confidence"
            ] = min(
                1.0,
                result.get(
                    "retrieval_confidence",
                    0.0,
                )
                + prior,
            )

            result[
                "metadata_prior"
            ] = prior

        results.sort(
            key=lambda item: (
                item.get(
                    "intent_match",
                    False,
                ),
                item.get(
                    "retrieval_confidence",
                    0.0,
                ),
                item.get(
                    "rerank_score",
                    0.0,
                ),
            ),
            reverse=True,
        )

        logger.info(
            "Metadata prior applied."
        )

        return results
    

    def _apply_document_prior(
        self,
        results: list[dict],
    ) -> list[dict]:
        """
        Production semantic document prior.

        Multiple highly-ranked chunks from the same document
        increase confidence that the document is the correct
        source.

        This score is intentionally small so it complements,
        rather than overrides, semantic relevance.
        """

        if not results:
            return results

        document_counts: dict[str, int] = {}

        for result in results:

            work_item_id = (
                result.get(
                    "metadata",
                    {},
                ).get(
                    "work_item_id",
                )
            )

            if work_item_id is None:
                continue

            document_counts[
                work_item_id
            ] = (
                document_counts.get(
                    work_item_id,
                    0,
                )
                + 1
            )

        max_count = max(
            document_counts.values(),
            default=1,
        )

        for result in results:

            work_item_id = (
                result.get(
                    "metadata",
                    {},
                ).get(
                    "work_item_id",
                )
            )

            count = document_counts.get(
                work_item_id,
                1,
            )

            prior = (
                count
                / max_count
            ) * 0.15

            result[
                "document_prior"
            ] = prior

            result[
                "retrieval_confidence"
            ] = min(
                1.0,
                result.get(
                    "retrieval_confidence",
                    0.0,
                )
                + prior,
            )

        results.sort(
            key=lambda item: (
                item.get(
                    "retrieval_confidence",
                    0.0,
                ),
                item.get(
                    "rerank_score",
                    0.0,
                ),
            ),
            reverse=True,
        )

        logger.info(
            "Semantic document prior applied."
        )

        return results


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

            intent_match = intent in filename

            if intent_match:
                score += 1.0

            result["rrf_score"] = score
            result["intent_match"] = intent_match

            boosted.append(result)

        boosted.sort(
            key=lambda x: x.get(
                "rrf_score",
                0.0,
            ),
            reverse=True,
        )

        return boosted
    
    def _should_balance_context(
        self,
        results: list[dict],
    ) -> bool:
        """
        Decide whether cross-document context balancing
        should be applied.
        """

        if len(results) <= 1:
            return False

        top_document = (
            results[0]
            .get("metadata", {})
            .get("work_item_id")
        )

        if top_document is None:
            return True

        top_document_chunks = sum(
            1
            for result in results
            if (
                result.get("metadata", {}).get("work_item_id")
                == top_document
            )
        )

        return top_document_chunks < 3
    

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
                    float("-inf"),
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