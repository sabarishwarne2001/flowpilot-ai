"""
Central Retrieval Service, strictly partitioned by workspace.
ARCH-11.5 Step 4 & 6: Staged execution timing and confident intent boosting.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any, Optional, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.request_context import stage
from app.services.document_filter_service import document_filter_service
from app.services.hybrid_search_service import hybrid_search_service
from app.services.intent_service import intent_service
from app.services.reranker_client import reranker_client

logger = logging.getLogger("app.services.retrieval")


class RetrievalService:
    """
    Production retrieval abstraction scoped per workspace.
    """

    def hybrid_search(
        self,
        *,
        workspace_id: UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int,
        similarity_threshold: float,
        db: Session | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if db is None:
            raise ValueError("RetrievalService.hybrid_search requires an active database session.")

        with stage("retrieval.intent"):
            match = intent_service.detect(query, db=db, workspace_id=workspace_id)

        # `is not None`, not truthiness. An empty sequence is a caller saying
        # "restrict to these zero documents", which must retrieve nothing --
        # collapsing it to None here would silently widen the search to the
        # whole workspace instead. This matches lexical_search_service and
        # hybrid_search_service, which use the same test; this call site was
        # the one that still relied on truthiness.
        filtered_ids = list(work_item_ids) if work_item_ids is not None else None

        if filtered_ids == []:
            # Nothing in scope. Short-circuit rather than issuing a query whose
            # WHERE clause is a tautologically empty IN (), which some backends
            # accept and some reject, and which no reader can distinguish from
            # a legitimately empty result.
            return []

        with stage("retrieval.hybrid_sql") as details:
            outcome = hybrid_search_service.search(
                db,
                workspace_id=workspace_id,
                query=query,
                work_item_ids=filtered_ids,
                top_k=max(top_k, settings.RERANK_MAX_CANDIDATES),
                similarity_threshold=similarity_threshold,
            )
            details["returned"] = len(outcome.results)
            merged_results = outcome.results

        if settings.INTENT_BOOST_ENABLED and match.confident:
            merged_results = self._boost_intent_documents(merged_results, match.intent)

        with stage("rerank") as details:
            merged_results = reranker_client.rerank(
                query=query, results=merged_results, request_id=request_id
            )
            details["status"] = merged_results[0].get("rerank_status") if merged_results else None

        merged_results = self._estimate_retrieval_confidence(merged_results)
        merged_results = self._apply_metadata_prior(query=query, results=merged_results)
        merged_results = self._apply_document_prior(merged_results)

        merged_results = document_filter_service.filter_documents(merged_results)

        document_count = len({str(result.get("metadata", {}).get("work_item_id")) for result in merged_results})
        if document_count > 1 and self._should_balance_context(merged_results):
            merged_results = self._balance_documents(merged_results)

        logger.info(
            "Hybrid retrieval pipeline completed with %d final chunk(s).",
            len(merged_results),
        )
        return merged_results

    def _estimate_retrieval_confidence(
        self,
        results: list[dict],
    ) -> list[dict]:
        if not results:
            return results

        rerank_scores = [
            float(result.get("rerank_score", 0.0))
            for result in results
            if result.get("rerank_score") is not None
        ]
        semantic_scores = [float(result.get("similarity_score", 0.0)) for result in results]
        lexical_scores = [float(result.get("lexical_score", 0.0)) for result in results]

        max_rerank = max(rerank_scores) if rerank_scores else 1.0
        min_rerank = min(rerank_scores) if rerank_scores else 0.0
        max_lexical = max(lexical_scores) if lexical_scores else 1.0

        rerank_range = max(max_rerank - min_rerank, 1e-6)
        lexical_range = max(max_lexical, 1e-6)

        for result in results:
            raw_rerank = result.get("rerank_score")
            rerank = (float(raw_rerank) - min_rerank) / rerank_range if raw_rerank is not None else 0.5
            semantic = float(result.get("similarity_score", 0.0))
            lexical = (float(result.get("lexical_score", 0.0))) / lexical_range

            confidence = (0.55 * rerank + 0.30 * semantic + 0.15 * lexical)
            result["retrieval_confidence"] = max(0.0, min(confidence, 1.0))

        return results

    def _apply_metadata_prior(
        self,
        *,
        query: str,
        results: list[dict],
    ) -> list[dict]:
        if not results:
            return results

        query_words = {word.lower() for word in query.split() if len(word) >= 3}

        for result in results:
            metadata = result.get("metadata", {})
            filename = metadata.get("original_filename", "").lower()
            prior = 0.0

            filename_tokens = {token for token in filename.replace("_", " ").replace("-", " ").split()}
            overlap = len(query_words & filename_tokens)

            if overlap:
                prior += min(overlap * 0.35, 1.00)

            result["retrieval_confidence"] = min(1.0, result.get("retrieval_confidence", 0.0) + prior)
            result["metadata_prior"] = prior

        results.sort(
            key=lambda item: (
                item.get("intent_match", False),
                item.get("retrieval_confidence", 0.0),
                item.get("rerank_score", float("-inf")) if item.get("rerank_score") is not None else float("-inf"),
            ),
            reverse=True,
        )
        return results

    def _apply_document_prior(
        self,
        results: list[dict],
    ) -> list[dict]:
        if not results:
            return results

        document_counts: dict[str, int] = {}
        for result in results:
            raw_id = result.get("metadata", {}).get("work_item_id")
            if not raw_id:
                continue
            wid = str(raw_id)
            document_counts[wid] = document_counts.get(wid, 0) + 1

        max_count = max(document_counts.values(), default=1)

        for result in results:
            raw_id = result.get("metadata", {}).get("work_item_id")
            wid = str(raw_id) if raw_id else ""
            count = document_counts.get(wid, 1)
            prior = (count / max_count) * 0.15

            result["document_prior"] = prior
            result["retrieval_confidence"] = min(1.0, result.get("retrieval_confidence", 0.0) + prior)

        results.sort(
            key=lambda item: (
                item.get("retrieval_confidence", 0.0),
                item.get("rerank_score", float("-inf")) if item.get("rerank_score") is not None else float("-inf"),
            ),
            reverse=True,
        )
        return results

    def _boost_intent_documents(
        self,
        results: list[dict],
        intent: str,
    ) -> list[dict]:
        if intent == "unknown":
            return results

        boosted = []
        for result in results:
            metadata = result.get("metadata", {})
            filename = metadata.get("original_filename", "").lower()
            score = result.get("rrf_score", 0.0)
            intent_match = intent in filename

            if intent_match:
                score += 1.0

            result["rrf_score"] = score
            result["intent_match"] = intent_match
            boosted.append(result)

        boosted.sort(
            key=lambda x: x.get("rrf_score", 0.0),
            reverse=True,
        )
        return boosted

    def _should_balance_context(
        self,
        results: list[dict],
    ) -> bool:
        if len(results) <= 1:
            return False

        top_document = results[0].get("metadata", {}).get("work_item_id")
        if top_document is None:
            return True

        top_document_chunks = sum(
            1
            for result in results
            if str(result.get("metadata", {}).get("work_item_id")) == str(top_document)
        )
        return top_document_chunks < 3

    def _balance_documents(
        self,
        results: list[dict],
    ) -> list[dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for result in results:
            metadata = result.get("metadata", {})
            work_item_id = str(metadata.get("work_item_id") or "")
            grouped[work_item_id].append(result)

        for chunks in grouped.values():
            chunks.sort(
                key=lambda item: item.get("rerank_score", float("-inf")) if item.get("rerank_score") is not None else float("-inf"),
                reverse=True,
            )

        balanced: list[dict] = []
        for work_item_id, chunks in grouped.items():
            grouped[work_item_id] = chunks[:settings.MAX_CONTEXT_CHUNKS_PER_DOCUMENT]

        while grouped:
            completed = []
            for work_item_id, chunks in grouped.items():
                if chunks:
                    balanced.append(chunks.pop(0))
                if not chunks:
                    completed.append(work_item_id)

            for work_item_id in completed:
                grouped.pop(work_item_id, None)

        return balanced


retrieval_service = RetrievalService()

__all__ = ["RetrievalService", "retrieval_service"]
